"""
Phase 5 tests — AgentDAO.

Uses the db_session fixture from conftest (joined transaction with SAVEPOINT
rollback) — all writes are isolated and never persist beyond the test.
"""

from __future__ import annotations

import uuid

import pytest

from app.dao.agent_dao import AgentDAO
from app.tools.base import ToolResult


def _sid() -> str:
    return f"sess-{uuid.uuid4()}"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class TestUpsertAndListSession:
    @pytest.mark.asyncio
    async def test_upsert_and_list_session(self, db_session):
        dao = AgentDAO(db_session)
        sid = _sid()
        await dao.upsert_session(sid, model="claude-haiku-4-5", metadata={"env": "test"})

        sessions = await dao.list_sessions(limit=50)
        found = [s for s in sessions if s["session_id"] == sid]
        assert len(found) == 1
        assert found[0]["model"] == "claude-haiku-4-5"
        assert isinstance(found[0]["metadata"], dict)
        assert found[0]["metadata"].get("env") == "test"

    @pytest.mark.asyncio
    async def test_upsert_is_idempotent(self, db_session):
        dao = AgentDAO(db_session)
        sid = _sid()
        await dao.upsert_session(sid, model="claude-haiku-4-5", metadata={})
        await dao.upsert_session(sid, model="claude-sonnet-4-6", metadata={})

        sessions = await dao.list_sessions(limit=100)
        matching = [s for s in sessions if s["session_id"] == sid]

        # Exactly one row despite two upserts
        assert len(matching) == 1
        # Model reflects the second (latest) upsert
        assert matching[0]["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

class TestAddAndGetMessages:
    @pytest.mark.asyncio
    async def test_add_and_get_messages_in_order(self, db_session):
        dao = AgentDAO(db_session)
        sid = _sid()
        await dao.upsert_session(sid, model="claude-haiku-4-5", metadata={})

        await dao.add_message(sid, role="user",      content="Hello",   turn_index=0)
        await dao.add_message(sid, role="assistant",  content="Hi",      turn_index=1)
        await dao.add_message(sid, role="user",      content="Tell me", turn_index=2)

        messages = await dao.get_messages(sid)
        assert len(messages) == 3
        assert [m["turn_index"] for m in messages] == [0, 1, 2]
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["content"] == "Tell me"

    @pytest.mark.asyncio
    async def test_get_messages_empty_for_unknown_session(self, db_session):
        dao = AgentDAO(db_session)
        messages = await dao.get_messages(_sid())
        assert messages == []


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------

class TestLogAndGetToolCalls:
    @pytest.mark.asyncio
    async def test_log_and_get_tool_calls_success(self, db_session):
        dao = AgentDAO(db_session)
        sid = _sid()
        await dao.upsert_session(sid, model="claude-haiku-4-5", metadata={})

        result = ToolResult(success=True, data={"k": "v"}, chart_spec=None)
        await dao.log_tool_call(
            session_id=sid,
            call_id="call-abc",
            tool_name="listening_stats",
            tool_input={"metric": "totals"},
            result=result,
            latency_ms=42,
            iteration=1,
        )

        calls = await dao.get_tool_calls(sid)
        assert len(calls) == 1
        c = calls[0]
        assert c["call_id"] == "call-abc"
        assert c["tool_name"] == "listening_stats"
        assert c["success"] is True
        assert c["latency_ms"] == 42
        assert c["iteration"] == 1
        # tool_output must be a dict, not a JSON string
        assert isinstance(c["tool_output"], dict)
        assert c["tool_output"] == {"k": "v"}
        # tool_input must also be a dict
        assert isinstance(c["tool_input"], dict)
        assert c["tool_input"] == {"metric": "totals"}

    @pytest.mark.asyncio
    async def test_log_tool_call_failure(self, db_session):
        dao = AgentDAO(db_session)
        sid = _sid()
        await dao.upsert_session(sid, model="claude-haiku-4-5", metadata={})

        result = ToolResult(success=False, data=None, error="timeout")
        await dao.log_tool_call(
            session_id=sid,
            call_id="call-fail",
            tool_name="listening_stats",
            tool_input={"metric": "totals"},
            result=result,
            latency_ms=5001,
            iteration=2,
        )

        calls = await dao.get_tool_calls(sid)
        assert len(calls) == 1
        c = calls[0]
        assert c["success"] is False
        assert c["error_message"] == "timeout"
        assert c["tool_output"] is None

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_ordered_by_created_at(self, db_session):
        dao = AgentDAO(db_session)
        sid = _sid()
        await dao.upsert_session(sid, model="claude-haiku-4-5", metadata={})

        for i in range(3):
            await dao.log_tool_call(
                session_id=sid,
                call_id=f"c{i}",
                tool_name="listening_stats",
                tool_input={"metric": "totals"},
                result=ToolResult(success=True, data={"i": i}),
                latency_ms=10 * i,
                iteration=i + 1,
            )

        calls = await dao.get_tool_calls(sid)
        assert len(calls) == 3
        assert [c["call_id"] for c in calls] == ["c0", "c1", "c2"]
