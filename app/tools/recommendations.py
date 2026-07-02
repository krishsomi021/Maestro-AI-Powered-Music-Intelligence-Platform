"""
GetRecommendationsTool — pgvector content-based recommendations from seed track names.

For each seed name, fetches its embedding from track_metadata (ILIKE, LIMIT 1 per seed).
Averages found embeddings into a centroid vector and performs cosine-similarity search,
excluding the seed tracks themselves.

Never SELECT * — the embedding column is large; only artist_name, track_name, score.
Uses the main async engine (trusted connection, parameterized query).
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

import json

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import BaseTool, ToolResult


def _parse_embedding(raw) -> list[float]:
    """
    Normalise whatever asyncpg returns for a pgvector column into a plain float list.
    asyncpg has no built-in pgvector codec, so the value arrives as a string like
    "[0.1,0.2,...]". psycopg2+register_vector() returns a Python list already.
    Handle both so the tool works regardless of driver.
    """
    if isinstance(raw, (list, np.ndarray)):
        return list(raw)
    if isinstance(raw, str):
        return json.loads(raw)
    return list(raw)

if TYPE_CHECKING:
    from app.config import Settings


DISCOVERY_CENTROID_TRACK_LIMIT = 50


class GetRecommendationsTool(BaseTool):
    name = "get_recommendations"
    description = (
        "Recommend tracks similar to one or more seed tracks using pgvector cosine "
        "similarity over 384-dim embeddings. Provide track names (case-insensitive). "
        "Returns ranked recommendations excluding the seed tracks. Set discovery_mode "
        "to search a broader external catalog (via Last.fm) of tracks the user has "
        "never heard, ranked against their taste-profile centroid, instead of the "
        "closed set of already-known tracks."
    )

    def __init__(self, db: AsyncSession, settings: "Settings") -> None:
        self._db = db
        self._settings = settings

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "seed_track_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "One or more track names to seed the recommendation.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of recommendations to return (default 10).",
                    "default": 10,
                },
                "discovery_mode": {
                    "type": "boolean",
                    "description": (
                        "If true, ignore seed_track_names and instead rank the "
                        "external_catalog (tracks the user has never heard) against "
                        "their overall taste-profile centroid. Default false."
                    ),
                    "default": False,
                },
            },
            "required": [],
        }

    async def execute(
        self,
        seed_track_names: list[str] | None = None,
        limit: int = 10,
        discovery_mode: bool = False,
        **_: Any,
    ) -> ToolResult:
        try:
            limit = min(int(limit), self._settings.agent_sql_row_cap)

            if discovery_mode:
                return await self._execute_discovery(limit)

            if not seed_track_names:
                return ToolResult(success=False, error="None of the seed tracks were found")

            # Fetch embeddings for each seed name (ILIKE for case tolerance, LIMIT 1 each).
            embeddings: list[list[float]] = []
            resolved_names: list[str] = []
            for name in seed_track_names:
                row = await self._db.execute(
                    text("""
                        SELECT track_name, embedding
                        FROM track_metadata
                        WHERE track_name ILIKE :name
                          AND embedding IS NOT NULL
                        LIMIT 1
                    """),
                    {"name": name},
                )
                found = row.first()
                if found is not None:
                    embeddings.append(_parse_embedding(found.embedding))
                    resolved_names.append(found.track_name)

            if not embeddings:
                return ToolResult(success=False, error="None of the seed tracks were found")

            centroid = np.mean(
                [np.array(e, dtype=np.float32) for e in embeddings], axis=0
            ).tolist()

            result = await self._db.execute(
                text("""
                    SELECT artist_name, track_name,
                           1 - (embedding <=> CAST(:centroid AS vector)) AS score
                    FROM track_metadata
                    WHERE embedding IS NOT NULL
                      AND track_name NOT ILIKE ANY(:seed_names)
                    ORDER BY embedding <=> CAST(:centroid AS vector)
                    LIMIT :limit
                """),
                {
                    "centroid": str(centroid),
                    "seed_names": resolved_names,
                    "limit": limit,
                },
            )
            recommendations = [
                {
                    "artist_name": row.artist_name,
                    "track_name": row.track_name,
                    "score": float(row.score),
                }
                for row in result.all()
            ]
            return ToolResult(
                success=True,
                data={"recommendations": recommendations},
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))

    async def _execute_discovery(self, limit: int) -> ToolResult:
        """
        Open-world discovery: rank external_catalog against a taste-profile centroid
        built from the user's top DISCOVERY_CENTROID_TRACK_LIMIT most-played tracks
        (by play count in listening_history), L2-normalized. Biasing toward the
        user's dominant genres is intended taste-based behavior, not a bug — a
        single centroid is a deliberate simplification; multi-cluster profiles are
        a future enhancement.

        The listening_history exclusion is a plain indexed anti-join on
        normalized_key (populated at write time on both sides), not a per-row
        function call — see app/core/normalization.py.
        """
        top_rows = await self._db.execute(
            text("""
                SELECT tm.embedding
                FROM track_metadata tm
                JOIN listening_history lh
                    ON lh.artist_name = tm.artist_name AND lh.track_name = tm.track_name
                WHERE tm.embedding IS NOT NULL
                GROUP BY tm.artist_name, tm.track_name, tm.embedding
                ORDER BY COUNT(*) DESC
                LIMIT :centroid_limit
            """),
            {"centroid_limit": DISCOVERY_CENTROID_TRACK_LIMIT},
        )
        top_embeddings = [_parse_embedding(row.embedding) for row in top_rows.all()]
        if not top_embeddings:
            return ToolResult(success=False, error="No listening history available to build a taste profile")

        centroid = np.mean(
            [np.array(e, dtype=np.float32) for e in top_embeddings], axis=0
        )
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm

        result = await self._db.execute(
            text("""
                SELECT ec.artist_name, ec.track_name, ec.lastfm_match_score,
                       1 - (ec.embedding <=> CAST(:centroid AS vector)) AS score
                FROM external_catalog ec
                LEFT JOIN listening_history lh ON lh.normalized_key = ec.normalized_key
                WHERE ec.embedding IS NOT NULL
                  AND lh.normalized_key IS NULL
                ORDER BY ec.embedding <=> CAST(:centroid AS vector)
                LIMIT :limit
            """),
            {"centroid": str(centroid.tolist()), "limit": limit},
        )
        recommendations = [
            {
                "artist_name": row.artist_name,
                "track_name": row.track_name,
                "similarity_score": float(row.score),
                "lastfm_match_score": row.lastfm_match_score,
            }
            for row in result.all()
        ]
        return ToolResult(
            success=True,
            data={"recommendations": recommendations},
        )
