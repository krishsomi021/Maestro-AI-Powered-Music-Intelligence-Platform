"""
PromptBuilder — builds the agent system prompt with live schema injection.

The schema (column names + types) and one sample row per table are queried
from the live database on every call, so the agent's knowledge stays current
without a code change when columns are added.

Design Decision #6: schema injected at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.config import Settings

# Columns to exclude from the injected schema. The vector(384) embedding
# column appears as data_type='USER-DEFINED' in information_schema and
# contains 384 floats — including it would blow up the context window.
_EXCLUDED_COLUMNS = frozenset({"embedding"})

# Tables whose schema and sample rows are injected into the prompt.
_AGENT_TABLES = ("listening_history", "track_metadata")

# track_metadata columns to SELECT for the sample row query (everything
# except the embedding column, which must not be fetched as 384 floats).
_TRACK_METADATA_SAFE_COLUMNS = (
    "id", "spotify_track_id", "artist_name", "track_name",
    "spotify_artist_id", "genres", "popularity", "enriched_at",
)


class PromptBuilder:
    def __init__(self, settings: "Settings") -> None:
        self._settings = settings

    async def build_system_prompt(self, db: AsyncSession) -> str:
        schemas = await self._fetch_schemas(db)
        samples = await self._fetch_samples(db)
        return _render(schemas, samples, self._settings.agent_local_tz)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fetch_schemas(
        self, db: AsyncSession
    ) -> dict[str, list[dict]]:
        """
        Returns {table_name: [{column_name, data_type, is_nullable}, ...]}
        for each agent table, with excluded columns filtered out.
        """
        # 'embedding' is hardcoded here (not a bind param) because asyncpg
        # does not support tuple bind params for NOT IN. The value matches
        # _EXCLUDED_COLUMNS and the actual column name in init.sql.
        sql = text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = :table_name
              AND column_name  != 'embedding'
            ORDER BY ordinal_position
        """)

        schemas: dict[str, list[dict]] = {}
        for table in _AGENT_TABLES:
            result = await db.execute(sql, {"table_name": table})
            schemas[table] = [dict(row._mapping) for row in result.all()]
        return schemas

    async def _fetch_samples(
        self, db: AsyncSession
    ) -> dict[str, dict | None]:
        """
        Returns {table_name: row_dict | None} for one sample row per table.
        LIMIT 1 — no ORDER BY to avoid a full table scan on large tables.
        """
        samples: dict[str, dict | None] = {}

        # listening_history — SELECT * is safe (no oversized columns)
        try:
            r = await db.execute(text("SELECT * FROM listening_history LIMIT 1"))
            row = r.first()
            samples["listening_history"] = dict(row._mapping) if row else None
        except Exception:
            samples["listening_history"] = None

        # track_metadata — explicitly name columns to exclude 'embedding'.
        # Wrapped in try/except because the table requires the pgvector
        # extension; it won't exist on a fresh DB without it.
        try:
            safe_cols = ", ".join(_TRACK_METADATA_SAFE_COLUMNS)
            r = await db.execute(
                text(f"SELECT {safe_cols} FROM track_metadata LIMIT 1")  # noqa: S608
            )
            row = r.first()
            samples["track_metadata"] = dict(row._mapping) if row else None
        except Exception:
            samples["track_metadata"] = None

        return samples


# ------------------------------------------------------------------
# Rendering
# ------------------------------------------------------------------

def _format_columns(columns: list[dict]) -> str:
    if not columns:
        return "  (no columns found)"
    lines = []
    for col in columns:
        nullable = "" if col["is_nullable"] == "NO" else " (nullable)"
        lines.append(f"  - {col['column_name']}  [{col['data_type']}{nullable}]")
    return "\n".join(lines)


def _format_sample(sample: dict | None) -> str:
    if sample is None:
        return "  (no sample rows available)"
    lines = []
    for k, v in sample.items():
        lines.append(f"  {k}: {v!r}")
    return "\n".join(lines)


def _render(
    schemas: dict[str, list[dict]],
    samples: dict[str, dict | None],
    local_tz: str,
) -> str:
    lh_cols = _format_columns(schemas.get("listening_history", []))
    tm_cols = _format_columns(schemas.get("track_metadata", []))
    lh_sample = _format_sample(samples.get("listening_history"))
    tm_sample = _format_sample(samples.get("track_metadata"))

    return f"""\
You are a personal Spotify listening analyst. You have direct access to the \
user's real Spotify listening history loaded into a PostgreSQL database. \
Answer questions with specificity — you are working with their actual data, \
not generic examples.

═══════════════════════════════════════════════════════════════
DATABASE SCHEMA
═══════════════════════════════════════════════════════════════

Table: listening_history
  Each row is one track play (end of listen, ≥30 s played to qualify).
{lh_cols}

Sample row from listening_history:
{lh_sample}

Table: track_metadata
  Enrichment data for tracks: Spotify IDs, genres, and semantic embeddings.
  Populated by the ETL enrichment phase — may be sparse if enrichment hasn't run.
{tm_cols}

Sample row from track_metadata:
{tm_sample}

═══════════════════════════════════════════════════════════════
IMPORTANT DATA NOTES
═══════════════════════════════════════════════════════════════

- end_time is stored as UTC TIMESTAMP WITHOUT TIME ZONE. The user's local \
timezone is {local_tz}. The pre-built listening_stats metrics already \
convert to local time automatically. If you write a raw SQL query via \
run_sql_query, apply timezone conversion: \
(end_time AT TIME ZONE 'UTC') AT TIME ZONE '{local_tz}'.

- ms_played is milliseconds. When presenting listening time to the user, \
convert: divide by 60000 for minutes, or 3600000 for hours.

- top_genres joins listening_history with track_metadata. If enrichment \
hasn't run yet, genre data may be sparse or empty — this is expected, \
not an error.

═══════════════════════════════════════════════════════════════
TOOL USAGE GUIDANCE
═══════════════════════════════════════════════════════════════

You have five tools. Use them in this order of preference:

1. listening_stats — PREFER THIS for any aggregate question \
(top tracks, top artists, play counts, hourly/daily/monthly patterns, totals). \
It runs safe, pre-built SQL and returns chart-ready results. Always try \
listening_stats first before reaching for run_sql_query.

2. get_listening_profile — Use at the start of broad taste or preference \
questions ("what kind of music am I into?", "describe my listening habits"). \
It bundles several aggregates in one call, giving you holistic context before \
you drill into specifics.

3. get_recommendations — Use for recommendation questions \
("what should I listen to next?", "suggest something similar to X"). \
Do NOT use run_sql_query to generate recommendations.

4. semantic_track_search — Use for vibe/mood/description queries \
("find me melancholy late-night tracks", "something energetic for working out"). \
It searches track embeddings by semantic similarity.

5. run_sql_query — The escape hatch. Use ONLY for questions that none of the \
pre-built metrics above can answer. It runs agent-generated SELECT statements \
against a read-only role with a row cap. Remember to apply the timezone \
conversion described above when querying by time of day.

When a tool returns a chart_spec, acknowledge the chart in your answer \
(e.g. "As you can see in the chart above...").

═══════════════════════════════════════════════════════════════
RESPONSE STYLE
═══════════════════════════════════════════════════════════════

- Lead with the direct answer, then provide supporting detail.
- Keep answers conversational — you are a personal assistant, not a SQL report.
- Use specific numbers from the data (play counts, ms_played converted to \
minutes/hours, dates).
- Follow-up questions should resolve against earlier turns in the conversation \
(e.g. "which of those has the most plays?" refers to the prior answer's list).
- When data is unavailable or a table is empty, say so plainly — \
do not invent numbers.\
"""
