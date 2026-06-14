"""
Phase 5 tests — RedisMemoryManager.

Tests run against the real Redis instance (same broker URL used by Celery).
Each test generates a unique session_id to avoid cross-test interference.
The memory manager is closed after each test to avoid unclosed-connection warnings.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from app.agent.memory import RedisMemoryManager
from app.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sid() -> str:
    return f"pytest:{uuid.uuid4()}"


def _settings_with_max(max_messages: int) -> MagicMock:
    """Settings stub with overridden agent_max_messages."""
    s = MagicMock()
    s.celery_broker_url = settings.celery_broker_url
    s.agent_memory_ttl_seconds = 3600
    s.agent_max_messages = max_messages
    return s


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def memory():
    mgr = RedisMemoryManager(settings)
    yield mgr
    await mgr.aclose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoadEmpty:
    @pytest.mark.asyncio
    async def test_load_empty_returns_empty_list(self, memory):
        result = await memory.load(_sid())
        assert result == []


class TestAppendAndLoad:
    @pytest.mark.asyncio
    async def test_append_and_load_roundtrip(self, memory):
        sid = _sid()
        await memory.append_user_message(sid, "Hello")
        await memory.append_assistant_message(sid, "Hi there")
        history = await memory.load(sid)

        assert len(history) == 2
        assert history[0] == {"role": "user",      "content": "Hello"}
        assert history[1] == {"role": "assistant",  "content": "Hi there"}

        await memory.clear(sid)

    @pytest.mark.asyncio
    async def test_multiple_user_messages_ordered(self, memory):
        sid = _sid()
        for i in range(3):
            await memory.append_user_message(sid, f"msg {i}")
        history = await memory.load(sid)

        assert [m["content"] for m in history] == ["msg 0", "msg 1", "msg 2"]
        await memory.clear(sid)


class TestTruncation:
    @pytest.mark.asyncio
    async def test_truncation_drops_oldest(self):
        mgr = RedisMemoryManager(_settings_with_max(3))
        sid = _sid()
        try:
            for i in range(5):
                await mgr.append_user_message(sid, f"msg {i}")
            history = await mgr.load(sid)

            assert len(history) == 3
            # oldest two (msg 0, msg 1) dropped; most-recent three kept
            assert [m["content"] for m in history] == ["msg 2", "msg 3", "msg 4"]
        finally:
            await mgr.clear(sid)
            await mgr.aclose()

    @pytest.mark.asyncio
    async def test_at_exactly_max_no_truncation(self):
        mgr = RedisMemoryManager(_settings_with_max(3))
        sid = _sid()
        try:
            for i in range(3):
                await mgr.append_user_message(sid, f"msg {i}")
            history = await mgr.load(sid)
            assert len(history) == 3
        finally:
            await mgr.clear(sid)
            await mgr.aclose()


class TestClear:
    @pytest.mark.asyncio
    async def test_clear_deletes_key(self, memory):
        sid = _sid()
        await memory.append_user_message(sid, "to be cleared")
        await memory.clear(sid)
        result = await memory.load(sid)
        assert result == []

    @pytest.mark.asyncio
    async def test_clear_nonexistent_key_does_not_raise(self, memory):
        await memory.clear(_sid())  # must not raise


class TestTTL:
    @pytest.mark.asyncio
    async def test_ttl_is_set_after_write(self, memory):
        sid = _sid()
        await memory.append_user_message(sid, "ttl check")
        ttl = await memory._redis.ttl(memory._key(sid))
        assert ttl > 0
        await memory.clear(sid)
