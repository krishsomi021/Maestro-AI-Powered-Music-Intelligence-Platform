"""
RunSqlQueryTool — agent-generated SELECT queries via the agent_readonly role.

Safety is layered:
  1. sqlparse pre-filter: rejects multi-statement input and non-SELECT statement types.
  2. agent_readonly PostgreSQL role: the authoritative boundary — SELECT-only grants on
     listening_history and track_metadata. Even if sqlparse is fooled, the role cannot
     execute DDL/DML.

CTE note: sqlparse.get_type() returns "UNKNOWN" for WITH ... SELECT in many versions.
We allow an UNKNOWN statement whose stripped text starts with "WITH" and let the role
catch anything that isn't a real read query.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

import sqlparse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from app.config import Settings

# Module-level singleton — built once, reused across all requests.
_readonly_engine: AsyncEngine | None = None


def _get_readonly_engine(settings: "Settings") -> AsyncEngine:
    global _readonly_engine
    if _readonly_engine is None:
        url = (
            f"postgresql+asyncpg://{settings.agent_readonly_db_user}"
            f":{settings.agent_readonly_db_password}"
            f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
        )
        _readonly_engine = create_async_engine(url, echo=False)
    return _readonly_engine


class RunSqlQueryTool(BaseTool):
    name = "run_sql_query"
    description = (
        "Execute a single read-only SELECT query against the listening_history and "
        "track_metadata tables. Use this only when pre-built metrics cannot answer the "
        "question. The query runs under a restricted role with no write privileges and a "
        f"hard statement timeout. Results are capped at 200 rows."
    )

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A single SELECT SQL statement to execute.",
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str = "", **_: Any) -> ToolResult:
        # --- sqlparse pre-filter ---
        validation_error = _validate_query(query)
        if validation_error:
            return ToolResult(success=False, error=validation_error)

        # --- DB execution ---
        try:
            engine = _get_readonly_engine(self._settings)
            async with engine.connect() as conn:
                # SET does not accept bind parameters in asyncpg — interpolate the
                # validated int directly. Both SET and SELECT run on the same connection
                # so the timeout applies to this query.
                timeout_ms = int(self._settings.agent_sql_timeout_ms)
                await conn.execute(text(f"SET statement_timeout = {timeout_ms}"))

                result = await conn.execute(text(query))
                # Capture column names before the fetch to avoid any ambiguity.
                columns = list(result.keys())

                cap = self._settings.agent_sql_row_cap
                rows_raw = await result.fetchmany(cap + 1)

            truncated = len(rows_raw) > cap
            rows = [list(r) for r in rows_raw[:cap]]

            return ToolResult(
                success=True,
                data={"columns": columns, "rows": rows, "truncated": truncated},
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


def _validate_query(query: str) -> str | None:
    """Return an error string if the query fails pre-filter checks, else None."""
    stripped = query.strip()
    statements = [s for s in sqlparse.parse(stripped) if s.value.strip()]

    if len(statements) != 1:
        return "Only a single SELECT statement is permitted"

    stmt_type = statements[0].get_type()

    if stmt_type == "SELECT":
        return None

    # sqlparse returns "UNKNOWN" for CTEs (WITH ... SELECT) in many versions.
    # Allow them here; the agent_readonly role is the real enforcement boundary.
    if stmt_type == "UNKNOWN" and stripped.upper().startswith("WITH"):
        return None

    return "Only SELECT statements are permitted"
