"""
ListeningStatsTool — pre-built, parameterized aggregates over listening_history.

All date params are null-guarded:
  (:date_from IS NULL OR end_time >= :date_from)
  (:date_to   IS NULL OR end_time <= CAST(:date_to   AS TIMESTAMP))

Time-of-day and day-of-week extractions convert from UTC to local time first:
  EXTRACT(HOUR FROM end_time AT TIME ZONE :local_tz)

The timestamp column in listening_history is end_time (not ts).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import bindparam, text
from sqlalchemy.types import TIMESTAMP
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from app.config import Settings

_DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


class ListeningStatsTool(BaseTool):
    name = "listening_stats"
    description = (
        "Retrieve pre-built listening statistics from the user's Spotify history. "
        "Use this tool first for any aggregate question before reaching for run_sql_query. "
        "Metrics: top_tracks, top_artists, top_genres, listening_by_hour, "
        "listening_by_day_of_week, monthly_trends, totals. "
        "All date filters are optional — omit them to query all-time data."
    )

    def __init__(self, db: AsyncSession, settings: "Settings") -> None:
        self._db = db
        self._settings = settings

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": [
                        "top_tracks",
                        "top_artists",
                        "top_genres",
                        "listening_by_hour",
                        "listening_by_day_of_week",
                        "monthly_trends",
                        "totals",
                    ],
                    "description": "Which aggregate to compute.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows for top_* metrics (default 10).",
                    "default": 10,
                },
                "date_from": {
                    "type": "string",
                    "format": "date",
                    "description": "Inclusive start date (YYYY-MM-DD). Omit for all-time.",
                },
                "date_to": {
                    "type": "string",
                    "format": "date",
                    "description": "Inclusive end date (YYYY-MM-DD). Omit for all-time.",
                },
            },
            "required": ["metric"],
        }

    async def execute(self, metric: str, limit: int = 10, date_from: str | None = None, date_to: str | None = None, **_: Any) -> ToolResult:
        try:
            limit = min(int(limit), self._settings.agent_sql_row_cap)
            local_tz = self._settings.agent_local_tz
            params = {"date_from": date_from, "date_to": date_to, "local_tz": local_tz, "limit": limit}

            dispatch = {
                "top_tracks":             self._top_tracks,
                "top_artists":            self._top_artists,
                "top_genres":             self._top_genres,
                "listening_by_hour":      self._listening_by_hour,
                "listening_by_day_of_week": self._listening_by_day_of_week,
                "monthly_trends":         self._monthly_trends,
                "totals":                 self._totals,
            }
            handler = dispatch.get(metric)
            if handler is None:
                return ToolResult(success=False, error=f"Unknown metric: '{metric}'")

            return await handler(params)
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # Metric handlers
    # ------------------------------------------------------------------

    async def _top_tracks(self, params: dict) -> ToolResult:
        sql = text("""
            SELECT track_name, artist_name,
                   COUNT(*) AS play_count,
                   SUM(ms_played) AS total_ms_played
            FROM listening_history
            WHERE (:date_from IS NULL OR end_time >= :date_from)
              AND (:date_to   IS NULL OR end_time <= :date_to)
            GROUP BY track_name, artist_name
            ORDER BY play_count DESC
            LIMIT :limit
        """)
        rows = await self._fetch(sql, params)
        chart = {
            "chart_type": "bar",
            "x": [r["track_name"] for r in rows],
            "y": [r["play_count"] for r in rows],
            "title": "Top Tracks by Play Count",
        }
        return ToolResult(success=True, data=rows, chart_spec=chart)

    async def _top_artists(self, params: dict) -> ToolResult:
        sql = text("""
            SELECT artist_name,
                   COUNT(*) AS play_count,
                   SUM(ms_played) AS total_ms_played,
                   COUNT(DISTINCT track_name) AS unique_tracks
            FROM listening_history
            WHERE (:date_from IS NULL OR end_time >= :date_from)
              AND (:date_to   IS NULL OR end_time <= :date_to)
            GROUP BY artist_name
            ORDER BY play_count DESC
            LIMIT :limit
        """)
        rows = await self._fetch(sql, params)
        chart = {
            "chart_type": "bar",
            "x": [r["artist_name"] for r in rows],
            "y": [r["play_count"] for r in rows],
            "title": "Top Artists by Play Count",
        }
        return ToolResult(success=True, data=rows, chart_spec=chart)

    async def _top_genres(self, params: dict) -> ToolResult:
        sql = text("""
            SELECT genre,
                   COUNT(*) AS play_count
            FROM listening_history lh
            JOIN track_metadata tm
              ON lh.artist_name = tm.artist_name
             AND lh.track_name  = tm.track_name
            CROSS JOIN UNNEST(tm.genres) AS genre
            WHERE (:date_from IS NULL OR lh.end_time >= :date_from)
              AND (:date_to   IS NULL OR lh.end_time <= :date_to)
              AND tm.genres IS NOT NULL
              AND array_length(tm.genres, 1) > 0
            GROUP BY genre
            ORDER BY play_count DESC
            LIMIT :limit
        """)
        rows = await self._fetch(sql, params)
        chart = {
            "chart_type": "bar",
            "x": [r["genre"] for r in rows],
            "y": [r["play_count"] for r in rows],
            "title": "Top Genres by Play Count",
        }
        return ToolResult(success=True, data=rows, chart_spec=chart)

    async def _listening_by_hour(self, params: dict) -> ToolResult:
        # end_time is TIMESTAMP WITHOUT TIME ZONE storing UTC values.
        # Step 1: AT TIME ZONE 'UTC' → interpret as UTC, yielding TIMESTAMPTZ.
        # Step 2: AT TIME ZONE :local_tz → convert to local wall-clock time.
        # A single-step AT TIME ZONE :local_tz would go the wrong direction
        # (treating the stored value as local, converting it to UTC).
        sql = text("""
            SELECT EXTRACT(HOUR FROM (end_time AT TIME ZONE 'UTC') AT TIME ZONE :local_tz)::INTEGER AS hour,
                   COUNT(*) AS play_count
            FROM listening_history
            WHERE (:date_from IS NULL OR end_time >= :date_from)
              AND (:date_to   IS NULL OR end_time <= :date_to)
            GROUP BY hour
            ORDER BY hour
        """)
        rows = await self._fetch(sql, params)
        chart = {
            "chart_type": "bar",
            "x": [r["hour"] for r in rows],
            "y": [r["play_count"] for r in rows],
            "title": "Listening by Hour of Day (Local Time)",
        }
        return ToolResult(success=True, data=rows, chart_spec=chart)

    async def _listening_by_day_of_week(self, params: dict) -> ToolResult:
        # EXTRACT(DOW ...) returns 0=Sunday … 6=Saturday
        sql = text("""
            SELECT EXTRACT(DOW FROM (end_time AT TIME ZONE 'UTC') AT TIME ZONE :local_tz)::INTEGER AS day_of_week,
                   COUNT(*) AS play_count
            FROM listening_history
            WHERE (:date_from IS NULL OR end_time >= :date_from)
              AND (:date_to   IS NULL OR end_time <= :date_to)
            GROUP BY day_of_week
            ORDER BY day_of_week
        """)
        rows = await self._fetch(sql, params)
        chart = {
            "chart_type": "bar",
            "x": [_DOW_LABELS[r["day_of_week"]] for r in rows],
            "y": [r["play_count"] for r in rows],
            "title": "Listening by Day of Week",
        }
        return ToolResult(success=True, data=rows, chart_spec=chart)

    async def _monthly_trends(self, params: dict) -> ToolResult:
        sql = text("""
            SELECT TO_CHAR((end_time AT TIME ZONE 'UTC') AT TIME ZONE :local_tz, 'YYYY-MM') AS month,
                   COUNT(*) AS play_count,
                   SUM(ms_played) AS total_ms_played
            FROM listening_history
            WHERE (:date_from IS NULL OR end_time >= :date_from)
              AND (:date_to   IS NULL OR end_time <= :date_to)
            GROUP BY month
            ORDER BY month
        """)
        rows = await self._fetch(sql, params)
        chart = {
            "chart_type": "line",
            "x": [r["month"] for r in rows],
            "y": [r["play_count"] for r in rows],
            "title": "Monthly Listening Trends",
        }
        return ToolResult(success=True, data=rows, chart_spec=chart)

    async def _totals(self, params: dict) -> ToolResult:
        sql = text("""
            SELECT COUNT(*) AS total_plays,
                   SUM(ms_played) AS total_ms_played,
                   COUNT(DISTINCT artist_name) AS unique_artists,
                   COUNT(DISTINCT track_name) AS unique_tracks
            FROM listening_history
            WHERE (:date_from IS NULL OR end_time >= :date_from)
              AND (:date_to   IS NULL OR end_time <= :date_to)
        """)
        rows = await self._fetch(sql, params)
        return ToolResult(success=True, data=rows[0] if rows else {}, chart_spec=None)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    async def _fetch(self, sql: text, params: dict) -> list[dict]:
        # Coerce date strings to datetime so asyncpg sends a typed value.
        # Attach explicit TIMESTAMP bindparams so NULL is also sent with a
        # type OID — asyncpg raises AmbiguousParameterError without this.
        coerced = dict(params)
        for key in ("date_from", "date_to"):
            val = coerced.get(key)
            if isinstance(val, str):
                coerced[key] = datetime.fromisoformat(val)

        typed_sql = sql.bindparams(
            bindparam("date_from", type_=TIMESTAMP),
            bindparam("date_to", type_=TIMESTAMP),
        )
        result = await self._db.execute(typed_sql, coerced)
        return [dict(row._mapping) for row in result.all()]
