"""
RedisMemoryManager — hot conversation history for the agent loop.

Stores only conversation turns (user/assistant) in Redis.
Tool-call detail goes to Postgres (agent_tool_calls) — not here.

Key:   agent:session:{session_id}
Value: JSON-encoded list[{"role": str, "content": str}]
Write: always SETEX so every write refreshes the TTL.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import redis.asyncio

if TYPE_CHECKING:
    from app.config import Settings


class RedisMemoryManager:
    def __init__(self, settings: "Settings") -> None:
        self._redis = redis.asyncio.from_url(
            settings.celery_broker_url, decode_responses=True
        )
        self._ttl = settings.agent_memory_ttl_seconds
        self._max = settings.agent_max_messages

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load(self, session_id: str) -> list[dict]:
        raw = await self._redis.get(self._key(session_id))
        if raw is None:
            return []
        return json.loads(raw)

    async def append_user_message(self, session_id: str, content: str) -> None:
        await self._append(session_id, {"role": "user", "content": content})

    async def append_assistant_message(self, session_id: str, content: str) -> None:
        await self._append(session_id, {"role": "assistant", "content": content})

    async def clear(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))

    async def aclose(self) -> None:
        await self._redis.aclose()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _key(self, session_id: str) -> str:
        return f"agent:session:{session_id}"

    async def _append(self, session_id: str, message: dict) -> None:
        history = await self.load(session_id)
        history.append(message)
        if len(history) > self._max:
            history = history[-self._max:]
        await self._redis.setex(self._key(session_id), self._ttl, json.dumps(history))
