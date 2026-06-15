"""
SemanticTrackSearchTool — natural-language cosine search over track_metadata embeddings.

Reuses the cached SentenceTransformer("all-MiniLM-L6-v2") singleton from
worker/ml/embeddings.py — the same model used during ETL enrichment. The encode()
call runs in a thread-pool executor so it does not block the async event loop.

Queries the main (trusted) async engine via the injected db session.
Never SELECT * — the embedding column is large; only artist_name, track_name, score.
"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from app.config import Settings


class SemanticTrackSearchTool(BaseTool):
    name = "semantic_track_search"
    description = (
        "Search for tracks using a natural-language description. Embeds the query with "
        "the same sentence-transformer model used to index track_metadata and returns "
        "the closest matches by cosine similarity. Use this when the user describes a "
        "mood, genre, or style rather than a specific title."
    )

    def __init__(self, db: AsyncSession, settings: "Settings") -> None:
        self._db = db
        self._settings = settings

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language description of the tracks to find.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of results to return (default 10).",
                    "default": 10,
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str = "", limit: int = 10, **_: Any) -> ToolResult:
        try:
            limit = min(int(limit), self._settings.agent_sql_row_cap)

            # Encode in a thread-pool executor — SentenceTransformer.encode() is synchronous.
            from worker.ml.embeddings import _load_embedding_model
            model = _load_embedding_model()
            vec: list[float] = await asyncio.get_event_loop().run_in_executor(
                None, lambda: model.encode(query).tolist()
            )

            sql = text("""
                SELECT artist_name, track_name,
                       1 - (embedding <=> CAST(:vec AS vector)) AS score
                FROM track_metadata
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT :limit
            """)
            result = await self._db.execute(sql, {"vec": str(vec), "limit": limit})
            tracks = [
                {
                    "artist_name": row.artist_name,
                    "track_name": row.track_name,
                    "score": float(row.score),
                }
                for row in result.all()
            ]
            return ToolResult(success=True, data={"tracks": tracks})
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
