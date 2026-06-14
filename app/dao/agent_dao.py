"""
AgentDAO — durable persistence for agent sessions, messages, and tool-call audit.

Commit convention: matches app/api/dao/job_dao.py — db.commit() inline after each
write. Under tests/conftest.py's db_session fixture (join_transaction_mode=
"create_savepoint"), these commits become SAVEPOINT releases that are rolled back
when the outer transaction rolls back on teardown, so isolation is preserved.

JSONB binding: asyncpg raises AmbiguousParameterError when binding None to an
untyped JSONB parameter. All JSONB params use bindparam(type_=JSONB) so SQLAlchemy
sends the correct type OID even for NULL values.

JSONB readback: raw text() SELECTs may return JSONB columns as JSON strings rather
than dicts. All JSONB columns are defensively json.loads'd on readback.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolResult


class AgentDAO:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def upsert_session(
        self, session_id: str, model: str, metadata: dict
    ) -> None:
        sql = text("""
            INSERT INTO agent_sessions (session_id, model, metadata)
            VALUES (:session_id, :model, :metadata)
            ON CONFLICT (session_id) DO UPDATE SET
                model      = EXCLUDED.model,
                updated_at = clock_timestamp()
        """).bindparams(bindparam("metadata", type_=JSONB))
        await self._db.execute(sql, {
            "session_id": session_id,
            "model": model,
            "metadata": metadata,
        })
        await self._db.commit()

    async def list_sessions(self, limit: int = 20) -> list[dict]:
        sql = text("""
            SELECT session_id, model, metadata, created_at, updated_at
            FROM agent_sessions
            ORDER BY updated_at DESC
            LIMIT :limit
        """)
        result = await self._db.execute(sql, {"limit": limit})
        rows = []
        for row in result.all():
            d = dict(row._mapping)
            if isinstance(d.get("metadata"), str):
                d["metadata"] = json.loads(d["metadata"])
            rows.append(d)
        return rows

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def add_message(
        self, session_id: str, role: str, content: str, turn_index: int
    ) -> None:
        sql = text("""
            INSERT INTO agent_messages (session_id, role, content, turn_index)
            VALUES (:session_id, :role, :content, :turn_index)
        """)
        await self._db.execute(sql, {
            "session_id": session_id,
            "role": role,
            "content": content,
            "turn_index": turn_index,
        })
        await self._db.commit()

    async def get_messages(self, session_id: str) -> list[dict]:
        sql = text("""
            SELECT id, session_id, role, content, turn_index, created_at
            FROM agent_messages
            WHERE session_id = :session_id
            ORDER BY turn_index ASC
        """)
        result = await self._db.execute(sql, {"session_id": session_id})
        return [dict(row._mapping) for row in result.all()]

    # ------------------------------------------------------------------
    # Tool-call audit
    # ------------------------------------------------------------------

    async def log_tool_call(
        self,
        *,
        session_id: str,
        call_id: str | None,
        tool_name: str,
        tool_input: dict,
        result: ToolResult,
        latency_ms: int,
        iteration: int,
    ) -> None:
        sql = text("""
            INSERT INTO agent_tool_calls
                (session_id, call_id, tool_name, tool_input, tool_output,
                 latency_ms, success, error_message, iteration)
            VALUES
                (:session_id, :call_id, :tool_name, :tool_input, :tool_output,
                 :latency_ms, :success, :error_message, :iteration)
        """).bindparams(
            bindparam("tool_input",  type_=JSONB),
            bindparam("tool_output", type_=JSONB),
        )
        await self._db.execute(sql, {
            "session_id":    session_id,
            "call_id":       call_id,
            "tool_name":     tool_name,
            "tool_input":    tool_input,
            "tool_output":   result.data,
            "latency_ms":    latency_ms,
            "success":       result.success,
            "error_message": result.error,
            "iteration":     iteration,
        })
        await self._db.commit()

    async def get_tool_calls(self, session_id: str) -> list[dict]:
        sql = text("""
            SELECT session_id, call_id, tool_name, tool_input, tool_output,
                   latency_ms, success, error_message, iteration, created_at
            FROM agent_tool_calls
            WHERE session_id = :session_id
            ORDER BY created_at ASC
        """)
        result = await self._db.execute(sql, {"session_id": session_id})
        rows = []
        for row in result.all():
            d = dict(row._mapping)
            if isinstance(d.get("tool_input"), str):
                d["tool_input"] = json.loads(d["tool_input"])
            if isinstance(d.get("tool_output"), str):
                d["tool_output"] = json.loads(d["tool_output"])
            rows.append(d)
        return rows
