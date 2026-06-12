"""
Phase 1 tests — LLM client abstraction.

All tests mock the Anthropic SDK so no real API key is required.
Verifies:
  - text_delta events arrive in order
  - tool_use blocks are assembled from input_json_delta events and surfaced once complete
  - message_stop is always the final event with correct stop_reason and token counts
  - RateLimitError maps to LLMRateLimitError
  - APIStatusError maps to LLMAPIError
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from app.llm.anthropic_client import AnthropicProvider
from app.llm.base import (
    LLMAPIError,
    LLMRateLimitError,
    MessageStopEvent,
    TextDeltaEvent,
    ToolUseEvent,
)


# ---------------------------------------------------------------------------
# Helpers to build fake Anthropic stream events
# ---------------------------------------------------------------------------

def _msg_start(input_tokens: int = 10):
    e = MagicMock()
    e.type = "message_start"
    e.message.usage.input_tokens = input_tokens
    return e


def _block_start_text(index: int = 0):
    e = MagicMock()
    e.type = "content_block_start"
    e.index = index
    e.content_block.type = "text"
    return e


def _block_start_tool(index: int, tool_id: str, tool_name: str):
    e = MagicMock()
    e.type = "content_block_start"
    e.index = index
    e.content_block.type = "tool_use"
    e.content_block.id = tool_id
    e.content_block.name = tool_name
    return e


def _text_delta(index: int, text: str):
    e = MagicMock()
    e.type = "content_block_delta"
    e.index = index
    e.delta.type = "text_delta"
    e.delta.text = text
    return e


def _json_delta(index: int, partial_json: str):
    e = MagicMock()
    e.type = "content_block_delta"
    e.index = index
    e.delta.type = "input_json_delta"
    e.delta.partial_json = partial_json
    return e


def _block_stop(index: int):
    e = MagicMock()
    e.type = "content_block_stop"
    e.index = index
    return e


def _msg_delta(stop_reason: str = "end_turn", output_tokens: int = 20):
    e = MagicMock()
    e.type = "message_delta"
    e.delta.stop_reason = stop_reason
    e.usage.output_tokens = output_tokens
    return e


def _msg_stop():
    e = MagicMock()
    e.type = "message_stop"
    return e


def _make_stream(raw_events: list):
    """
    Build an AsyncContextManager whose __aiter__ yields raw_events.
    Patches AsyncAnthropic.messages.stream.
    """
    async def _aiter():
        for ev in raw_events:
            yield ev

    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
    stream_cm.__aexit__ = AsyncMock(return_value=False)
    stream_cm.__aiter__ = lambda self: _aiter()
    return stream_cm


async def _collect(provider: AnthropicProvider, messages=None, tools=None):
    events = []
    async for ev in provider.stream(
        messages=messages or [{"role": "user", "content": "hi"}],
        system="sys",
        tools=tools or [],
    ):
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTextDeltaOrdering:
    @pytest.mark.asyncio
    async def test_text_deltas_arrive_in_order(self):
        raw = [
            _msg_start(),
            _block_start_text(0),
            _text_delta(0, "Hello"),
            _text_delta(0, ", "),
            _text_delta(0, "world"),
            _block_stop(0),
            _msg_delta(),
            _msg_stop(),
        ]
        provider = AnthropicProvider(api_key="test", model="claude-haiku-4-5")
        with patch.object(provider._client.messages, "stream", return_value=_make_stream(raw)):
            events = await _collect(provider)

        text_events = [e for e in events if isinstance(e, TextDeltaEvent)]
        assert [e.text for e in text_events] == ["Hello", ", ", "world"]

    @pytest.mark.asyncio
    async def test_message_stop_is_last_event(self):
        raw = [
            _msg_start(),
            _block_start_text(0),
            _text_delta(0, "hi"),
            _block_stop(0),
            _msg_delta(stop_reason="end_turn", output_tokens=5),
            _msg_stop(),
        ]
        provider = AnthropicProvider(api_key="test", model="claude-haiku-4-5")
        with patch.object(provider._client.messages, "stream", return_value=_make_stream(raw)):
            events = await _collect(provider)

        assert isinstance(events[-1], MessageStopEvent)

    @pytest.mark.asyncio
    async def test_message_stop_carries_token_counts(self):
        raw = [
            _msg_start(input_tokens=42),
            _block_start_text(0),
            _text_delta(0, "ok"),
            _block_stop(0),
            _msg_delta(stop_reason="end_turn", output_tokens=7),
            _msg_stop(),
        ]
        provider = AnthropicProvider(api_key="test", model="claude-haiku-4-5")
        with patch.object(provider._client.messages, "stream", return_value=_make_stream(raw)):
            events = await _collect(provider)

        stop = events[-1]
        assert isinstance(stop, MessageStopEvent)
        assert stop.input_tokens == 42
        assert stop.output_tokens == 7
        assert stop.stop_reason == "end_turn"


class TestToolUseAssembly:
    @pytest.mark.asyncio
    async def test_tool_use_assembled_from_json_deltas(self):
        # Simulate the SDK splitting the JSON across multiple deltas
        full_input = {"metric": "top_tracks", "limit": 10}
        json_str = json.dumps(full_input)
        mid = len(json_str) // 2

        raw = [
            _msg_start(),
            _block_start_tool(0, "tool_abc", "listening_stats"),
            _json_delta(0, json_str[:mid]),
            _json_delta(0, json_str[mid:]),
            _block_stop(0),
            _msg_delta(stop_reason="tool_use", output_tokens=15),
            _msg_stop(),
        ]
        provider = AnthropicProvider(api_key="test", model="claude-haiku-4-5")
        with patch.object(provider._client.messages, "stream", return_value=_make_stream(raw)):
            events = await _collect(provider)

        tool_events = [e for e in events if isinstance(e, ToolUseEvent)]
        assert len(tool_events) == 1
        assert tool_events[0].id == "tool_abc"
        assert tool_events[0].name == "listening_stats"
        assert tool_events[0].input == full_input

    @pytest.mark.asyncio
    async def test_tool_use_surfaced_once_complete_not_during_deltas(self):
        """ToolUseEvent must not appear until after content_block_stop."""
        raw = [
            _msg_start(),
            _block_start_tool(0, "tid", "run_sql_query"),
            _json_delta(0, '{"q":'),
            _json_delta(0, '"SELECT 1"}'),
            _block_stop(0),
            _msg_delta(stop_reason="tool_use"),
            _msg_stop(),
        ]
        provider = AnthropicProvider(api_key="test", model="claude-haiku-4-5")
        collected: list = []

        with patch.object(provider._client.messages, "stream", return_value=_make_stream(raw)):
            async for ev in provider.stream(
                messages=[{"role": "user", "content": "q"}],
                system="s",
                tools=[],
            ):
                collected.append(ev)

        tool_idx = next(i for i, e in enumerate(collected) if isinstance(e, ToolUseEvent))
        stop_idx = next(i for i, e in enumerate(collected) if isinstance(e, MessageStopEvent))
        assert tool_idx < stop_idx

    @pytest.mark.asyncio
    async def test_two_tool_calls_in_one_turn(self):
        tool1 = {"a": 1}
        tool2 = {"b": 2}
        raw = [
            _msg_start(),
            _block_start_tool(0, "id1", "listening_stats"),
            _json_delta(0, json.dumps(tool1)),
            _block_stop(0),
            _block_start_tool(1, "id2", "get_recommendations"),
            _json_delta(1, json.dumps(tool2)),
            _block_stop(1),
            _msg_delta(stop_reason="tool_use"),
            _msg_stop(),
        ]
        provider = AnthropicProvider(api_key="test", model="claude-haiku-4-5")
        with patch.object(provider._client.messages, "stream", return_value=_make_stream(raw)):
            events = await _collect(provider)

        tool_events = [e for e in events if isinstance(e, ToolUseEvent)]
        assert len(tool_events) == 2
        assert tool_events[0].name == "listening_stats"
        assert tool_events[1].name == "get_recommendations"

    @pytest.mark.asyncio
    async def test_empty_json_buf_falls_back_to_empty_dict(self):
        """A tool_use block with no json deltas should produce input={}."""
        raw = [
            _msg_start(),
            _block_start_tool(0, "tid", "get_listening_profile"),
            _block_stop(0),
            _msg_delta(stop_reason="tool_use"),
            _msg_stop(),
        ]
        provider = AnthropicProvider(api_key="test", model="claude-haiku-4-5")
        with patch.object(provider._client.messages, "stream", return_value=_make_stream(raw)):
            events = await _collect(provider)

        tool_events = [e for e in events if isinstance(e, ToolUseEvent)]
        assert len(tool_events) == 1
        assert tool_events[0].input == {}


class TestErrorMapping:
    @pytest.mark.asyncio
    async def test_rate_limit_error_raises_llm_rate_limit_error(self):
        provider = AnthropicProvider(api_key="test", model="claude-haiku-4-5")

        async def _bad_stream(*args, **kwargs):
            raise anthropic.RateLimitError(
                "rate limited",
                response=MagicMock(status_code=429),
                body={},
            )

        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(side_effect=anthropic.RateLimitError(
            "rate limited", response=MagicMock(status_code=429), body={}
        ))
        stream_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider._client.messages, "stream", return_value=stream_cm):
            with pytest.raises(LLMRateLimitError):
                async for _ in provider.stream(
                    messages=[{"role": "user", "content": "hi"}],
                    system="s",
                    tools=[],
                ):
                    pass

    @pytest.mark.asyncio
    async def test_api_status_error_raises_llm_api_error(self):
        provider = AnthropicProvider(api_key="test", model="claude-haiku-4-5")

        mock_response = MagicMock()
        mock_response.status_code = 500
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(side_effect=anthropic.APIStatusError(
            "server error", response=mock_response, body={}
        ))
        stream_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider._client.messages, "stream", return_value=stream_cm):
            with pytest.raises(LLMAPIError):
                async for _ in provider.stream(
                    messages=[{"role": "user", "content": "hi"}],
                    system="s",
                    tools=[],
                ):
                    pass


class TestFactory:
    def test_get_llm_provider_returns_anthropic(self):
        from app.llm.factory import get_llm_provider
        from app.llm.anthropic_client import AnthropicProvider

        with patch("app.llm.factory.settings") as mock_settings:
            mock_settings.llm_provider = "anthropic"
            mock_settings.agent_model = "claude-haiku-4-5"
            mock_settings.anthropic_api_key = "sk-test"
            provider = get_llm_provider()

        assert isinstance(provider, AnthropicProvider)

    def test_get_llm_provider_model_override(self):
        from app.llm.factory import get_llm_provider
        from app.llm.anthropic_client import AnthropicProvider

        with patch("app.llm.factory.settings") as mock_settings:
            mock_settings.llm_provider = "anthropic"
            mock_settings.agent_model = "claude-haiku-4-5"
            mock_settings.anthropic_api_key = "sk-test"
            provider = get_llm_provider(model="claude-sonnet-4-6")

        assert isinstance(provider, AnthropicProvider)
        assert provider._model == "claude-sonnet-4-6"

    def test_unknown_provider_raises(self):
        from app.llm.factory import get_llm_provider

        with patch("app.llm.factory.settings") as mock_settings:
            mock_settings.llm_provider = "groq"
            mock_settings.agent_model = "something"
            with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
                get_llm_provider()
