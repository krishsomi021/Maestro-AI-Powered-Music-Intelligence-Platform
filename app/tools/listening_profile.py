"""
GetListeningProfileTool — holistic listening profile in a single call.

Runs five aggregates concurrently via asyncio.gather:
  - top 5 artists by play count
  - top 3 genres (via track_metadata JOIN; only ~600 enriched tracks match — expected)
  - total ms_played
  - most_active_hour  (UTC → local_tz, two-step conversion)
  - most_active_day   (UTC → local_tz, two-step conversion; trimmed day name)

Time-of-day aggregates use a two-step AT TIME ZONE conversion:
  (end_time AT TIME ZONE 'UTC') AT TIME ZONE :local_tz
Step 1 interprets the stored TIMESTAMP WITHOUT TZ as UTC → yields TIMESTAMPTZ.
Step 2 converts to local wall-clock time. A single-step conversion goes the wrong
direction (treats stored value as local, converts it to UTC).

The top_genres JOIN is intentionally narrow: only listening_history rows that have
a matching track_metadata entry (by artist_name AND track_name) contribute. That is
roughly 600 of the ~7 000+ history rows. This is expected and documented.

chart_spec shape (consumed by Phase 8 frontend ChartRenderer):
  {"type": "bar", "title": "...", "x": [<labels>], "y": [<counts>]}
"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from app.config import Settings


class GetListeningProfileTool(BaseTool):
    name = "get_listening_profile"
    description = (
        "Return a holistic listening profile: top 5 artists, top 3 genres, total "
        "listening time, most active hour of the day, and most active day of the week. "
        "All time-of-day values are expressed in the configured local timezone. "
        "Use this as a high-level overview before drilling into specifics."
    )

    def __init__(self, db: AsyncSession, settings: "Settings") -> None:
        self._db = db
        self._settings = settings

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **_: Any) -> ToolResult:
        try:
            local_tz = self._settings.agent_local_tz
            (
                top_artists,
                top_genres,
                totals,
                active_hour,
                active_day,
            ) = await asyncio.gather(
                self._top_artists(local_tz),
                self._top_genres(),
                self._totals(),
                self._most_active_hour(local_tz),
                self._most_active_day(local_tz),
            )

            chart_spec = {
                "type": "bar",
                "title": "Top Artists by Play Count",
                "x": [a["artist_name"] for a in top_artists],
                "y": [a["play_count"] for a in top_artists],
            }

            return ToolResult(
                success=True,
                data={
                    "top_artists": top_artists,
                    "top_genres": top_genres,
                    "total_ms_played": totals,
                    "most_active_hour": active_hour,
                    "most_active_day": active_day,
                },
                chart_spec=chart_spec,
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # Sub-queries
    # ------------------------------------------------------------------

    async def _top_artists(self, local_tz: str) -> list[dict]:
        result = await self._db.execute(
            text("""
                SELECT artist_name, COUNT(*) AS play_count
                FROM listening_history
                GROUP BY artist_name
                ORDER BY play_count DESC
                LIMIT 5
            """),
        )
        return [{"artist_name": row.artist_name, "play_count": int(row.play_count)}
                for row in result.all()]

    async def _top_genres(self) -> list[dict]:
        # JOIN on both artist_name and track_name — not track_name alone — to avoid
        # cross-artist collisions. Only enriched tracks (~600) have genre metadata.
        result = await self._db.execute(
            text("""
                SELECT genre, COUNT(*) AS play_count
                FROM listening_history lh
                JOIN track_metadata tm
                  ON lh.artist_name = tm.artist_name
                 AND lh.track_name  = tm.track_name
                CROSS JOIN UNNEST(tm.genres) AS genre
                WHERE tm.genres IS NOT NULL
                  AND array_length(tm.genres, 1) > 0
                GROUP BY genre
                ORDER BY play_count DESC
                LIMIT 3
            """),
        )
        return [{"genre": row.genre, "play_count": int(row.play_count)}
                for row in result.all()]

    async def _totals(self) -> int:
        result = await self._db.execute(
            text("SELECT COALESCE(SUM(ms_played), 0) AS total FROM listening_history"),
        )
        row = result.first()
        return int(row.total) if row else 0

    async def _most_active_hour(self, local_tz: str) -> int:
        result = await self._db.execute(
            text("""
                SELECT EXTRACT(HOUR FROM
                    (end_time AT TIME ZONE 'UTC') AT TIME ZONE :local_tz
                )::INT AS hour,
                COUNT(*) AS play_count
                FROM listening_history
                GROUP BY hour
                ORDER BY play_count DESC
                LIMIT 1
            """),
            {"local_tz": local_tz},
        )
        row = result.first()
        return int(row.hour) if row else 0

    async def _most_active_day(self, local_tz: str) -> str:
        result = await self._db.execute(
            text("""
                SELECT TRIM(TO_CHAR(
                    (end_time AT TIME ZONE 'UTC') AT TIME ZONE :local_tz,
                    'Day'
                )) AS day_name,
                COUNT(*) AS play_count
                FROM listening_history
                GROUP BY day_name
                ORDER BY play_count DESC
                LIMIT 1
            """),
            {"local_tz": local_tz},
        )
        row = result.first()
        return row.day_name if row else ""
