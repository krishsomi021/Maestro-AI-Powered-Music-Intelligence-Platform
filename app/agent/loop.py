"""
AgentLoop — pure ReAct loop, no FastAPI imports.

Single streaming mode: every LLM iteration is streamed. Text deltas forward to the
caller as text_chunk events; tool_use blocks are accumulated silently, executed once
complete, then appended as tool results before the next iteration.

The final answer is generated exactly once — there is no non-streaming complete() path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

from app.llm.base import LLMProvider, StreamEvent


# ---------------------------------------------------------------------------
# Event types emitted by AgentLoop.run()
# ---------------------------------------------------------------------------

@dataclass
class TextChunkEvent:
    type: Literal["text_chunk"] = field(default="text_chunk", init=False)
    text: str = ""


@dataclass
class ToolStartEvent:
    type: Literal["tool_start"] = field(default="tool_start", init=False)
    call_id: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    iteration: int = 1


@dataclass
class ToolEndEvent:
    type: Literal["tool_end"] = field(default="tool_end", init=False)
    call_id: str = ""
    tool_name: str = ""
    success: bool = True
    data: Any = None
    error: str | None = None
    chart_spec: dict[str, Any] | None = None
    latency_ms: int = 0
    iteration: int = 1


@dataclass
class DoneEvent:
    type: Literal["done"] = field(default="done", init=False)
    total_iterations: int = 1


@dataclass
class ErrorEvent:
    type: Literal["error"] = field(default="error", init=False)
    message: str = ""


AgentEvent = TextChunkEvent | ToolStartEvent | ToolEndEvent | DoneEvent | ErrorEvent


# ---------------------------------------------------------------------------
# AgentLoop
# ---------------------------------------------------------------------------

class AgentLoop:
    """
    ReAct-style agent loop. Depends on an LLMProvider and a ToolRegistry-like
    object that exposes .schemas (list[dict]) and an async .execute(name, input)
    returning a ToolResult.

    Designed to be FastAPI-free so it is unit-testable without the web layer.
    """

    def __init__(
        self,
        llm: LLMProvider,
        tool_registry: Any,
        max_iterations: int = 6,
        on_tool_call: Any | None = None,
    ) -> None:
        self._llm = llm
        self._registry = tool_registry
        self._max_iterations = max_iterations
        self._on_tool_call = on_tool_call

    async def run(
        self,
        messages: list[dict],
        system: str,
        session_id: str,
    ) -> AsyncIterator[AgentEvent]:
        """
        Stream events for a single agent turn.

        on_tool_call (constructor dep) — optional async callable for audit
        persistence; errors are silently ignored so they never surface to the caller.
        Signature: (*, session_id, call_id, tool_name, tool_input, result,
        latency_ms, iteration) -> None
        """
        # Work on a local copy so the caller's list is not mutated.
        msgs: list[dict] = list(messages)
        tools = self._registry.schemas if self._registry else []

        for iteration in range(1, self._max_iterations + 1):
            text_produced = False
            tool_calls_this_iter: list[dict] = []

            # --- stream one LLM iteration -----------------------------------
            async for event in self._llm.stream(msgs, system, tools):
                if event.type == "text_delta":
                    text_produced = True
                    yield TextChunkEvent(text=event.text)

                elif event.type == "tool_use":
                    tool_calls_this_iter.append({
                        "id": event.id,
                        "name": event.name,
                        "input": event.input,
                    })

                elif event.type == "message_stop":
                    # stop_reason and usage are on the event; nothing to yield here
                    pass

            # --- pure-text iteration → answer already streamed → done ------
            if not tool_calls_this_iter:
                if text_produced:
                    yield DoneEvent(total_iterations=iteration)
                    return
                # LLM produced nothing (edge case); treat as done
                yield DoneEvent(total_iterations=iteration)
                return

            # --- tool iteration: execute each call, collect results ---------
            tool_result_contents: list[dict] = []

            for tc in tool_calls_this_iter:
                call_id = tc["id"]
                tool_name = tc["name"]
                tool_input = tc["input"]

                yield ToolStartEvent(
                    call_id=call_id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    iteration=iteration,
                )

                t_start = time.monotonic()
                result = await self._registry.execute(tool_name, tool_input)
                latency_ms = int((time.monotonic() - t_start) * 1000)

                yield ToolEndEvent(
                    call_id=call_id,
                    tool_name=tool_name,
                    success=result.success,
                    data=result.data,
                    error=result.error,
                    chart_spec=result.chart_spec,
                    latency_ms=latency_ms,
                    iteration=iteration,
                )

                # Audit callback — never let it crash the loop
                if self._on_tool_call is not None:
                    try:
                        await self._on_tool_call(
                            session_id=session_id,
                            call_id=call_id,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            result=result,
                            latency_ms=latency_ms,
                            iteration=iteration,
                        )
                    except Exception:
                        pass

                # Build the tool_result content block for the next message
                result_text = result.error if not result.success else _serialise(result.data)
                tool_result_contents.append({
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": result_text,
                })

            # Append the assistant turn (with tool_use blocks) + tool results
            msgs.append({
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]}
                    for tc in tool_calls_this_iter
                ],
            })
            msgs.append({
                "role": "user",
                "content": tool_result_contents,
            })

            # Continue to the next iteration

        # Guard exhausted without a text-only iteration
        yield ErrorEvent(
            message=f"Agent reached max_iterations ({self._max_iterations}) without a final answer."
        )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _serialise(data: Any) -> str:
    """Convert tool result data to a string for the tool_result content block."""
    import json

    if data is None:
        return ""
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, default=str)
    except (TypeError, ValueError):
        return str(data)
