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

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from app.config import Settings


class GetRecommendationsTool(BaseTool):
    name = "get_recommendations"
    description = (
        "Recommend tracks similar to one or more seed tracks using pgvector cosine "
        "similarity over 384-dim embeddings. Provide track names (case-insensitive). "
        "Returns ranked recommendations excluding the seed tracks."
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
            },
            "required": ["seed_track_names"],
        }

    async def execute(
        self,
        seed_track_names: list[str] | None = None,
        limit: int = 10,
        **_: Any,
    ) -> ToolResult:
        try:
            if not seed_track_names:
                return ToolResult(success=False, error="None of the seed tracks were found")

            limit = min(int(limit), self._settings.agent_sql_row_cap)

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
                    embeddings.append(list(found.embedding))
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
