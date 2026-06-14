"""
Phase 6 tests — Agent API endpoints.

The LLM is mocked so no Anthropic API calls are made. Real DB (db_session
savepoint fixture) and real Redis are used, so writes go through the full stack
but are rolled back / keyed uniquely per test.

Rate limiting is disabled in the test environment (RATE_LIMIT_ENABLED=False set
in conftest.py os.environ.setdefault).

Every test that POSTs a message uses a unique uuid4 session_id and asserts
membership / shape — never exact counts — because other sessions may coexist.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.dao.agent_dao import AgentDAO
from app.llm.base import MessageStopEvent, TextDeltaEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sid() -> str:
    return f"test-{uuid.uuid4()}"


def _make_text_llm(text: str) -> MagicMock:
    """Mock LLMProvider that emits a single text delta then message_stop."""
    async def _stream(*args, **kwargs):
        e = TextDeltaEvent()
        e.text = text
        yield e
        s = MessageStopEvent()
        s.stop_reason = "end_turn"
        s.input_tokens = 5
        s.output_tokens = 10
        yield s

    llm = MagicMock()
    llm.stream = _stream
    return llm


def _parse_sse(text: str) -> list[dict]:
    """Parse SSE body into a list of event dicts."""
    return [
        json.loads(line[6:])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


# ---------------------------------------------------------------------------
# POST /agent/message
# ---------------------------------------------------------------------------

class TestPostMessage:
    @pytest.mark.asyncio
    async def test_streams_text_chunk_and_done(self, client):
        sid = _sid()
        mock_llm = _make_text_llm("Hello from the agent!")

        with patch("app.agent.service.get_llm_provider", return_value=mock_llm):
            response = await client.post(
                "/agent/message",
                json={"session_id": sid, "user_message": "Hi"},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        events = _parse_sse(response.text)
        types = [e["type"] for e in events]
        assert "text_chunk" in types
        assert "done" in types

    @pytest.mark.asyncio
    async def test_text_chunk_carries_correct_text(self, client):
        sid = _sid()
        mock_llm = _make_text_llm("42 tracks total.")

        with patch("app.agent.service.get_llm_provider", return_value=mock_llm):
            response = await client.post(
                "/agent/message",
                json={"session_id": sid, "user_message": "Total plays?"},
            )

        events = _parse_sse(response.text)
        text_events = [e for e in events if e["type"] == "text_chunk"]
        combined = "".join(e["text"] for e in text_events)
        assert "42 tracks total." in combined

    @pytest.mark.asyncio
    async def test_missing_session_id_returns_422(self, client):
        response = await client.post(
            "/agent/message",
            json={"user_message": "Hi"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_session_id_returns_422(self, client):
        response = await client.post(
            "/agent/message",
            json={"session_id": "", "user_message": "Hi"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_session_id_over_255_chars_returns_422(self, client):
        response = await client.post(
            "/agent/message",
            json={"session_id": "x" * 256, "user_message": "Hi"},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /agent/session/{session_id}
# ---------------------------------------------------------------------------

class TestDeleteSession:
    @pytest.mark.asyncio
    async def test_delete_session_returns_204(self, client):
        response = await client.delete(f"/agent/session/{_sid()}")
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_nonexistent_session_returns_204(self, client):
        # Clear on a key that doesn't exist must still succeed
        response = await client.delete("/agent/session/does-not-exist")
        assert response.status_code == 204


# ---------------------------------------------------------------------------
# GET /agent/sessions
# ---------------------------------------------------------------------------

class TestGetSessions:
    @pytest.mark.asyncio
    async def test_get_sessions_returns_list(self, client, db_session):
        sid = _sid()
        await AgentDAO(db_session).upsert_session(
            sid, model="claude-haiku-4-5", metadata={}
        )

        response = await client.get("/agent/sessions")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        session_ids = [s["session_id"] for s in data]
        assert sid in session_ids

    @pytest.mark.asyncio
    async def test_get_sessions_respects_limit(self, client, db_session):
        response = await client.get("/agent/sessions?limit=1")
        assert response.status_code == 200
        assert len(response.json()) <= 1


# ---------------------------------------------------------------------------
# GET /agent/sessions/{session_id}/messages
# ---------------------------------------------------------------------------

class TestGetMessages:
    @pytest.mark.asyncio
    async def test_get_messages_returns_list(self, client, db_session):
        sid = _sid()
        dao = AgentDAO(db_session)
        await dao.upsert_session(sid, model="claude-haiku-4-5", metadata={})
        await dao.add_message(sid, role="user", content="Hello", turn_index=0)

        response = await client.get(f"/agent/sessions/{sid}/messages")
        assert response.status_code == 200
        messages = response.json()
        assert any(m["content"] == "Hello" for m in messages)

    @pytest.mark.asyncio
    async def test_get_messages_empty_for_unknown_session(self, client):
        response = await client.get(f"/agent/sessions/{_sid()}/messages")
        assert response.status_code == 200
        assert response.json() == []


# ---------------------------------------------------------------------------
# GET /agent/sessions/{session_id}/tool-calls
# ---------------------------------------------------------------------------

class TestGetToolCalls:
    @pytest.mark.asyncio
    async def test_get_tool_calls_returns_list(self, client, db_session):
        from app.tools.base import ToolResult

        sid = _sid()
        dao = AgentDAO(db_session)
        await dao.upsert_session(sid, model="claude-haiku-4-5", metadata={})
        await dao.log_tool_call(
            session_id=sid,
            call_id="call-1",
            tool_name="listening_stats",
            tool_input={"metric": "totals"},
            result=ToolResult(success=True, data={"total_plays": 100}),
            latency_ms=50,
            iteration=1,
        )

        response = await client.get(f"/agent/sessions/{sid}/tool-calls")
        assert response.status_code == 200
        calls = response.json()
        assert any(c["tool_name"] == "listening_stats" for c in calls)

    @pytest.mark.asyncio
    async def test_get_tool_calls_empty_for_unknown_session(self, client):
        response = await client.get(f"/agent/sessions/{_sid()}/tool-calls")
        assert response.status_code == 200
        assert response.json() == []
