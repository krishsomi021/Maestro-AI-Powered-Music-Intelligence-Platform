"""
Phase 9 tests — AgentService.

All LLM, registry, and Redis calls are mocked. Real DB session (db_session
SAVEPOINT fixture) is used to verify turn_index derivation from Postgres.

Coverage:
  - turn_index for new user message is derived from Postgres row count, not Redis
  - Redis is NOT written when the loop yields ErrorEvent (no DoneEvent)
  - memory.aclose() is called in the finally block even when the loop errors
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.agent.loop import DoneEvent, ErrorEvent, TextChunkEvent
from app.agent.service import AgentService
from app.config import settings
from app.llm.base import MessageStopEvent, TextDeltaEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sid() -> str:
    return f"svc-test-{uuid.uuid4()}"


def _make_mock_memory():
    mem = MagicMock()
    mem.load = AsyncMock(return_value=[])
    mem.append_user_message = AsyncMock()
    mem.append_assistant_message = AsyncMock()
    mem.aclose = AsyncMock()
    mem.clear = AsyncMock()
    return mem


def _make_text_llm(text: str) -> MagicMock:
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


def _make_error_llm() -> MagicMock:
    async def _stream(*args, **kwargs):
        raise RuntimeError("Anthropic API unavailable")
        yield  # makes it an async generator

    llm = MagicMock()
    llm.stream = _stream
    return llm


def _make_mock_registry() -> MagicMock:
    reg = MagicMock()
    reg.schemas = []
    reg.execute = AsyncMock()
    return reg


def _make_mock_prompt_builder(system: str = "sys") -> MagicMock:
    pb = MagicMock()
    pb.build_system_prompt = AsyncMock(return_value=system)
    return pb


async def _drain(ait) -> list:
    return [ev async for ev in ait]


# ---------------------------------------------------------------------------
# Helpers for patching all external deps at once
# ---------------------------------------------------------------------------

def _patch_service_deps(mock_llm, mock_memory, mock_registry=None, mock_pb=None):
    """Context manager that patches all AgentService constructor deps."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("app.agent.service.get_llm_provider", return_value=mock_llm))
    rmm_patch = stack.enter_context(patch("app.agent.service.RedisMemoryManager"))
    rmm_patch.return_value = mock_memory
    if mock_registry is not None:
        stack.enter_context(patch("app.agent.service.build_default_registry", return_value=mock_registry))
    if mock_pb is not None:
        pb_patch = stack.enter_context(patch("app.agent.service.PromptBuilder"))
        pb_patch.return_value = mock_pb
    return stack


# ---------------------------------------------------------------------------
# turn_index derived from Postgres
# ---------------------------------------------------------------------------

class TestTurnIndexFromPostgres:
    @pytest.mark.asyncio
    async def test_user_turn_index_equals_existing_message_count(self, db_session):
        """turn_index passed to dao.add_message equals the count of rows already in
        Postgres for that session — not anything from Redis."""
        sid = _sid()
        mock_memory = _make_mock_memory()
        mock_llm = _make_text_llm("response")
        mock_registry = _make_mock_registry()
        mock_pb = _make_mock_prompt_builder()

        # Simulate 2 messages already in Postgres for this session via a mock DAO.
        existing = [{"role": "user", "content": "msg0"}, {"role": "assistant", "content": "r0"}]
        mock_dao = MagicMock()
        mock_dao.upsert_session = AsyncMock()
        mock_dao.get_messages = AsyncMock(return_value=existing)
        mock_dao.add_message = AsyncMock()
        mock_dao.log_tool_call = AsyncMock()

        with patch("app.agent.service.get_llm_provider", return_value=mock_llm), \
             patch("app.agent.service.build_default_registry", return_value=mock_registry), \
             patch("app.agent.service.RedisMemoryManager") as MockRMM, \
             patch("app.agent.service.PromptBuilder") as MockPB:
            MockRMM.return_value = mock_memory
            MockPB.return_value = mock_pb

            service = AgentService(db_session, settings)
            service._dao = mock_dao  # replace after init to inject mock

            await _drain(service.handle_turn(sid, "new message"))

        # First add_message call is the user turn; turn_index must equal len(existing) = 2
        first_call = mock_dao.add_message.call_args_list[0]
        assert first_call.kwargs["role"] == "user"
        assert first_call.kwargs["turn_index"] == 2


# ---------------------------------------------------------------------------
# No Redis write on loop failure
# ---------------------------------------------------------------------------

class TestNoRedisWriteOnLoopFailure:
    @pytest.mark.asyncio
    async def test_append_not_called_when_llm_raises(self, db_session):
        """When the LLM raises (yielding ErrorEvent), succeeded stays False and Redis
        is never written."""
        sid = _sid()
        mock_memory = _make_mock_memory()
        mock_llm = _make_error_llm()
        mock_registry = _make_mock_registry()
        mock_pb = _make_mock_prompt_builder()

        mock_dao = MagicMock()
        mock_dao.upsert_session = AsyncMock()
        mock_dao.get_messages = AsyncMock(return_value=[])
        mock_dao.add_message = AsyncMock()
        mock_dao.log_tool_call = AsyncMock()

        with patch("app.agent.service.get_llm_provider", return_value=mock_llm), \
             patch("app.agent.service.build_default_registry", return_value=mock_registry), \
             patch("app.agent.service.RedisMemoryManager") as MockRMM, \
             patch("app.agent.service.PromptBuilder") as MockPB:
            MockRMM.return_value = mock_memory
            MockPB.return_value = mock_pb

            service = AgentService(db_session, settings)
            service._dao = mock_dao

            events = await _drain(service.handle_turn(sid, "hello"))

        # Loop yields ErrorEvent, not DoneEvent → succeeded=False → no Redis write
        assert any(isinstance(e, ErrorEvent) for e in events)
        mock_memory.append_user_message.assert_not_called()
        mock_memory.append_assistant_message.assert_not_called()


# ---------------------------------------------------------------------------
# memory.aclose() called in finally
# ---------------------------------------------------------------------------

class TestMemoryAcloseInFinally:
    @pytest.mark.asyncio
    async def test_aclose_called_when_llm_raises(self, db_session):
        """memory.aclose() must be called in the finally block even when the loop
        terminates with an ErrorEvent rather than a DoneEvent."""
        sid = _sid()
        mock_memory = _make_mock_memory()
        mock_llm = _make_error_llm()
        mock_registry = _make_mock_registry()
        mock_pb = _make_mock_prompt_builder()

        mock_dao = MagicMock()
        mock_dao.upsert_session = AsyncMock()
        mock_dao.get_messages = AsyncMock(return_value=[])
        mock_dao.add_message = AsyncMock()
        mock_dao.log_tool_call = AsyncMock()

        with patch("app.agent.service.get_llm_provider", return_value=mock_llm), \
             patch("app.agent.service.build_default_registry", return_value=mock_registry), \
             patch("app.agent.service.RedisMemoryManager") as MockRMM, \
             patch("app.agent.service.PromptBuilder") as MockPB:
            MockRMM.return_value = mock_memory
            MockPB.return_value = mock_pb

            service = AgentService(db_session, settings)
            service._dao = mock_dao

            await _drain(service.handle_turn(sid, "hello"))

        mock_memory.aclose.assert_awaited_once()
