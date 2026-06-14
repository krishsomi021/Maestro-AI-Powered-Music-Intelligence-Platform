"""
AgentService — orchestrates a single agent turn.

Per-request lifetime: instantiated inside the route handler, torn down when
the streaming response generator is exhausted or closed.

Turn flow:
  1. Upsert session row in Postgres.
  2. Load hot conversation history from Redis.
  3. Persist user message to Postgres (durable, up-front).
  4. Build system prompt (live schema injection).
  5. Run AgentLoop; yield all events to the caller.
  6. On success only: append user + assistant turns to Redis together so no
     orphaned half-turn is left if the loop fails or the client disconnects.
  7. Persist assistant message to Postgres.
  8. Close the Redis connection.

turn_index is derived from Postgres (not Redis) so it stays monotonic even
after Redis truncation drops old messages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop import AgentLoop, AgentEvent, DoneEvent, TextChunkEvent
from app.agent.memory import RedisMemoryManager
from app.agent.prompts import PromptBuilder
from app.dao.agent_dao import AgentDAO
from app.llm.factory import get_llm_provider
from app.tools.registry import build_default_registry

if TYPE_CHECKING:
    from app.config import Settings


class AgentService:
    def __init__(self, db: AsyncSession, settings: "Settings") -> None:
        self._db = db
        self._settings = settings
        self._dao = AgentDAO(db)
        self._memory = RedisMemoryManager(settings)
        self._llm = get_llm_provider()
        self._registry = build_default_registry(db, settings)
        self._prompt_builder = PromptBuilder(settings)

    async def handle_turn(
        self, session_id: str, user_message: str
    ) -> AsyncIterator[AgentEvent]:
        dao = self._dao
        memory = self._memory
        settings = self._settings

        # 1. Upsert session (idempotent)
        await dao.upsert_session(
            session_id, model=settings.agent_model, metadata={}
        )

        # 2. Hot history for the loop (Redis)
        loaded_history = await memory.load(session_id)

        # 3. Persist user message to Postgres — derive turn_index from DB so
        #    it stays monotonic even after Redis truncation.
        existing_msgs = await dao.get_messages(session_id)
        user_turn_index = len(existing_msgs)
        await dao.add_message(
            session_id, role="user", content=user_message,
            turn_index=user_turn_index,
        )

        # 4. Messages list for the loop = Redis history + current user turn
        messages_for_loop = loaded_history + [
            {"role": "user", "content": user_message}
        ]

        # 5. Build system prompt (queries live schema from DB)
        system = await self._prompt_builder.build_system_prompt(self._db)

        # 6. Audit callback — wired from DAO; errors silenced inside the loop
        async def _on_tool_call(
            *, session_id: str, call_id: str, tool_name: str,
            tool_input: dict, result, latency_ms: int, iteration: int,
        ) -> None:
            await dao.log_tool_call(
                session_id=session_id,
                call_id=call_id,
                tool_name=tool_name,
                tool_input=tool_input,
                result=result,
                latency_ms=latency_ms,
                iteration=iteration,
            )

        loop = AgentLoop(
            llm=self._llm,
            tool_registry=self._registry,
            max_iterations=settings.agent_max_iterations,
            on_tool_call=_on_tool_call,
        )

        # 7. Stream events; accumulate text for persistence
        text_chunks: list[str] = []
        succeeded = False
        try:
            async for event in loop.run(messages_for_loop, system, session_id):
                if isinstance(event, TextChunkEvent):
                    text_chunks.append(event.text)
                elif isinstance(event, DoneEvent):
                    succeeded = True
                yield event
        finally:
            # 8. On success: write user + assistant to Redis together so the
            #    hot history is never left with a half-turn.
            if succeeded and text_chunks:
                assistant_content = "".join(text_chunks)
                await memory.append_user_message(session_id, user_message)
                await memory.append_assistant_message(session_id, assistant_content)
                await dao.add_message(
                    session_id, role="assistant", content=assistant_content,
                    turn_index=user_turn_index + 1,
                )
            await memory.aclose()
