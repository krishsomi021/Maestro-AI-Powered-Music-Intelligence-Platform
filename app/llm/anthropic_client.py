"""
Anthropic implementation of LLMProvider.

Uses client.messages.stream() — the single streaming mode.
Tool-use blocks are assembled from streamed input_json_delta events and
surfaced as one complete ToolUseEvent only after content_block_stop.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import anthropic

from app.llm.base import (
    LLMAPIError,
    LLMProvider,
    LLMRateLimitError,
    MessageStopEvent,
    StreamEvent,
    TextDeltaEvent,
    ToolUseEvent,
)


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def stream(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        max_tokens: int = 2048,
    ) -> AsyncIterator[StreamEvent]:
        # Accumulate tool_use blocks by content-block index.
        # Each entry: {"id": str, "name": str, "json_buf": str}
        tool_blocks: dict[int, dict] = {}
        input_tokens = 0
        output_tokens = 0

        try:
            async with self._client.messages.stream(
                model=self._model,
                messages=messages,
                system=system,
                tools=tools if tools else anthropic.NOT_GIVEN,
                max_tokens=max_tokens,
            ) as stream:
                async for event in stream:
                    etype = event.type

                    if etype == "message_start":
                        usage = getattr(event.message, "usage", None)
                        if usage:
                            input_tokens = getattr(usage, "input_tokens", 0)

                    elif etype == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            tool_blocks[event.index] = {
                                "id": block.id,
                                "name": block.name,
                                "json_buf": "",
                            }

                    elif etype == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield TextDeltaEvent(text=delta.text)
                        elif delta.type == "input_json_delta":
                            if event.index in tool_blocks:
                                tool_blocks[event.index]["json_buf"] += delta.partial_json

                    elif etype == "content_block_stop":
                        block_data = tool_blocks.pop(event.index, None)
                        if block_data is not None:
                            try:
                                parsed = json.loads(block_data["json_buf"] or "{}")
                            except json.JSONDecodeError:
                                parsed = {}
                            yield ToolUseEvent(
                                id=block_data["id"],
                                name=block_data["name"],
                                input=parsed,
                            )

                    elif etype == "message_delta":
                        usage = getattr(event, "usage", None)
                        if usage:
                            output_tokens = getattr(usage, "output_tokens", 0)
                        stop_reason = getattr(event.delta, "stop_reason", "") or ""

                    elif etype == "message_stop":
                        pass

        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            raise LLMAPIError(f"{exc.status_code}: {exc.message}") from exc

        # Derive stop_reason from the final message via get_final_message if needed,
        # but we already captured it from message_delta. Fall back gracefully.
        final_stop_reason = locals().get("stop_reason", "end_turn") or "end_turn"
        yield MessageStopEvent(
            stop_reason=final_stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
