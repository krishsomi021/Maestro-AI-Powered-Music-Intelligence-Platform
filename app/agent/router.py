"""
Agent router — POST /agent/message (SSE stream) + session/audit GET endpoints.

SSE format: each AgentEvent is emitted as a single frame:
    data: {json}\n\n

The client consumes the POST response body via fetch() + ReadableStream reader.
No EventSource — EventSource is GET-only and would replay a charged LLM turn on
reconnect.

DELETE /agent/session/{session_id} clears the Redis hot-memory key only.
Postgres audit rows (agent_messages, agent_tool_calls) intentionally persist so
the session history and tool-call log remain retrievable via the GET endpoints
after the chat window is cleared.
"""

import dataclasses
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop import AgentEvent
from app.config import settings
from app.core.rate_limit import limiter
from app.dao.agent_dao import AgentDAO
from app.db.async_session import get_db

router = APIRouter(prefix="/agent", tags=["agent"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class MessageRequest(BaseModel):
    session_id: str
    user_message: str

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("session_id must be non-empty")
        if len(v) > 255:
            raise ValueError("session_id must be ≤ 255 characters (VARCHAR(255))")
        return v


# ---------------------------------------------------------------------------
# SSE serialization
# ---------------------------------------------------------------------------

def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _serialize_event(event: AgentEvent) -> str:
    """Convert an AgentEvent dataclass to an SSE data line."""
    d = dataclasses.asdict(event)
    return f"data: {json.dumps(d, default=_json_default)}\n\n"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/message")
@limiter.limit("10/minute")
async def post_message(
    request: Request,
    body: MessageRequest,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    from app.agent.service import AgentService

    service = AgentService(db=db, settings=settings)

    async def event_stream():
        async for event in service.handle_turn(body.session_id, body.user_message):
            yield _serialize_event(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.delete("/session/{session_id}", status_code=204, response_class=Response)
async def delete_session(session_id: str) -> Response:
    """
    Clears the Redis hot-memory key for this session (new-chat button).
    Postgres audit rows (messages, tool calls) are intentionally preserved.
    """
    from app.agent.memory import RedisMemoryManager
    memory = RedisMemoryManager(settings)
    try:
        await memory.clear(session_id)
    finally:
        await memory.aclose()
    return Response(status_code=204)


@router.get("/sessions")
async def list_sessions(
    limit: int = 20, db: AsyncSession = Depends(get_db)
) -> list[dict]:
    return await AgentDAO(db).list_sessions(limit=limit)


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict]:
    return await AgentDAO(db).get_messages(session_id)


@router.get("/sessions/{session_id}/tool-calls")
async def get_tool_calls(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict]:
    return await AgentDAO(db).get_tool_calls(session_id)
