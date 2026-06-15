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

    @pytest.mark.asyncio
    async def test_chart_spec_propagates_through_sse(self, client):
        """chart_spec attached to a ToolResult flows through the full SSE stack and
        appears in the tool_end frame received by the client."""
        from app.llm.base import ToolUseEvent
        from app.tools.base import ToolResult

        sid = _sid()
        chart = {"type": "bar", "title": "Top Artists", "x": ["A", "B"], "y": [10, 5]}

        # Build two call sequences: first yields a tool_use, second yields the text answer.
        async def _tool_then_text(*args, **kwargs):
            call_count = getattr(_tool_then_text, "_n", 0)
            _tool_then_text._n = call_count + 1
            if call_count == 0:
                e = ToolUseEvent()
                e.id = "chart-call-1"
                e.name = "listening_stats"
                e.input = {"metric": "top_artists"}
                yield e
                s = MessageStopEvent()
                s.stop_reason = "tool_use"
                s.input_tokens = 5
                s.output_tokens = 5
                yield s
            else:
                t = TextDeltaEvent()
                t.text = "Here are your top artists."
                yield t
                s = MessageStopEvent()
                s.stop_reason = "end_turn"
                s.input_tokens = 5
                s.output_tokens = 10
                yield s

        _tool_then_text._n = 0
        mock_llm = MagicMock()
        mock_llm.stream = _tool_then_text

        mock_registry = MagicMock()
        mock_registry.schemas = []
        mock_registry.execute = AsyncMock(return_value=ToolResult(
            success=True,
            data=[{"artist_name": "A", "play_count": 10}],
            chart_spec=chart,
        ))

        with patch("app.agent.service.get_llm_provider", return_value=mock_llm), \
             patch("app.agent.service.build_default_registry", return_value=mock_registry):
            response = await client.post(
                "/agent/message",
                json={"session_id": sid, "user_message": "Top artists?"},
            )

        assert response.status_code == 200
        events = _parse_sse(response.text)
        tool_end_events = [e for e in events if e["type"] == "tool_end"]
        assert len(tool_end_events) >= 1
        assert tool_end_events[0]["chart_spec"] == chart


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

    @pytest.mark.asyncio
    async def test_delete_clears_redis_but_postgres_persists(self, client, db_session):
        """DELETE /agent/session/{id} clears Redis hot memory only.
        Postgres audit rows survive; a subsequent POST starts fresh (empty Redis
        history) with turn_index derived from Postgres."""
        sid = _sid()
        mock_llm = _make_text_llm("first response")

        # First turn — populates Redis and Postgres
        with patch("app.agent.service.get_llm_provider", return_value=mock_llm):
            r1 = await client.post(
                "/agent/message",
                json={"session_id": sid, "user_message": "Hello"},
            )
        assert r1.status_code == 200

        # DELETE clears Redis hot memory only
        del_response = await client.delete(f"/agent/session/{sid}")
        assert del_response.status_code == 204

        # Postgres audit rows still present
        msgs_response = await client.get(f"/agent/sessions/{sid}/messages")
        assert msgs_response.status_code == 200
        messages = msgs_response.json()
        assert len(messages) >= 1  # at minimum the user turn is durable

        # A subsequent POST on the same session_id succeeds — turn_index picks up
        # from Postgres; the loop runs with empty Redis history (fresh context window)
        mock_llm2 = _make_text_llm("second response")
        with patch("app.agent.service.get_llm_provider", return_value=mock_llm2):
            r2 = await client.post(
                "/agent/message",
                json={"session_id": sid, "user_message": "Second message"},
            )
        assert r2.status_code == 200
        events2 = _parse_sse(r2.text)
        assert any(e["type"] == "done" for e in events2)


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
