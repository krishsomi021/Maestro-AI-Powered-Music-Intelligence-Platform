"""
Phase 4 tests — AgentLoop.

All LLM and tool calls are mocked. No network, no DB, no FastAPI.

Coverage:
  - Pure text turn (no tool calls)
  - Single tool call → result → final text answer
  - Multiple tool calls in one iteration
  - Unknown tool name (registry returns success=False)
  - Tool call with error result (loop continues, surfaces error to LLM)
  - Max iterations exhausted → ErrorEvent
  - chart_spec forwarded on ToolEndEvent
  - on_tool_call audit callback invoked with correct kwargs
  - on_tool_call exception silently ignored
  - on_tool_call NOT wired (None) — no crash
  - Local message list not mutated by run()
  - DoneEvent carries correct total_iterations
"""

from __future__ import annotations

import json
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.agent.loop import (
    AgentLoop,
    DoneEvent,
    ErrorEvent,
    TextChunkEvent,
    ToolEndEvent,
    ToolStartEvent,
)
from app.llm.base import MessageStopEvent, TextDeltaEvent, ToolUseEvent
from app.tools.base import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_use_event(id: str, name: str, input: dict) -> ToolUseEvent:
    e = ToolUseEvent()
    e.id = id
    e.name = name
    e.input = input
    return e


def _text_delta(text: str) -> TextDeltaEvent:
    e = TextDeltaEvent()
    e.text = text
    return e


def _message_stop(stop_reason: str = "end_turn") -> MessageStopEvent:
    e = MessageStopEvent()
    e.stop_reason = stop_reason
    e.input_tokens = 10
    e.output_tokens = 20
    return e


async def _stream(*events) -> AsyncIterator:
    for e in events:
        yield e


def _mock_llm(*event_sequences):
    """
    Returns a mock LLMProvider whose stream() returns successive event sequences.
    Each positional arg is an iterable of events for one call to stream().
    """
    llm = MagicMock()
    # Each call to stream() must return a fresh async generator.
    side_effects = [_stream(*seq) for seq in event_sequences]
    llm.stream = MagicMock(side_effect=side_effects)
    return llm


def _mock_registry(tool_results: dict[str, ToolResult] | None = None):
    """
    Returns a mock ToolRegistry. tool_results maps tool name → ToolResult.
    Unknown names return ToolResult(success=False, error="Unknown tool").
    """
    results = tool_results or {}

    registry = MagicMock()
    registry.schemas = [{"name": "listening_stats", "description": "...", "input_schema": {}}]

    async def execute(name: str, input_dict: dict) -> ToolResult:
        return results.get(name, ToolResult(success=False, error=f"Unknown tool: '{name}'"))

    registry.execute = AsyncMock(side_effect=execute)
    return registry


async def _collect(loop: AgentLoop, messages, system="sys", session_id="s1"):
    events = []
    async for ev in loop.run(messages, system, session_id):
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Pure text turn
# ---------------------------------------------------------------------------

class TestPureTextTurn:
    @pytest.mark.asyncio
    async def test_yields_text_chunks_then_done(self):
        llm = _mock_llm([_text_delta("Hello "), _text_delta("world"), _message_stop()])
        loop = AgentLoop(llm=llm, tool_registry=_mock_registry())
        events = await _collect(loop, [])

        text_events = [e for e in events if isinstance(e, TextChunkEvent)]
        assert len(text_events) == 2
        assert text_events[0].text == "Hello "
        assert text_events[1].text == "world"

    @pytest.mark.asyncio
    async def test_done_event_emitted(self):
        llm = _mock_llm([_text_delta("hi"), _message_stop()])
        loop = AgentLoop(llm=llm, tool_registry=_mock_registry())
        events = await _collect(loop, [])

        done = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done) == 1

    @pytest.mark.asyncio
    async def test_done_event_total_iterations_is_1(self):
        llm = _mock_llm([_text_delta("hi"), _message_stop()])
        loop = AgentLoop(llm=llm, tool_registry=_mock_registry())
        events = await _collect(loop, [])

        done = next(e for e in events if isinstance(e, DoneEvent))
        assert done.total_iterations == 1

    @pytest.mark.asyncio
    async def test_no_tool_events_on_pure_text(self):
        llm = _mock_llm([_text_delta("answer"), _message_stop()])
        loop = AgentLoop(llm=llm, tool_registry=_mock_registry())
        events = await _collect(loop, [])

        assert not any(isinstance(e, (ToolStartEvent, ToolEndEvent)) for e in events)


# ---------------------------------------------------------------------------
# Single tool call → final answer
# ---------------------------------------------------------------------------

class TestSingleToolCall:
    @pytest.mark.asyncio
    async def test_tool_start_and_end_emitted(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()],
            [_text_delta("You played 42 tracks."), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={"total_plays": 42})})
        loop = AgentLoop(llm=llm, tool_registry=registry)
        events = await _collect(loop, [])

        starts = [e for e in events if isinstance(e, ToolStartEvent)]
        ends = [e for e in events if isinstance(e, ToolEndEvent)]
        assert len(starts) == 1
        assert len(ends) == 1

    @pytest.mark.asyncio
    async def test_tool_start_carries_correct_fields(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()],
            [_text_delta("done"), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={})})
        loop = AgentLoop(llm=llm, tool_registry=registry)
        events = await _collect(loop, [])

        start = next(e for e in events if isinstance(e, ToolStartEvent))
        assert start.call_id == "c1"
        assert start.tool_name == "listening_stats"
        assert start.tool_input == {"metric": "totals"}
        assert start.iteration == 1

    @pytest.mark.asyncio
    async def test_tool_end_carries_result_fields(self):
        chart = {"chart_type": "bar", "x": [], "y": [], "title": "T"}
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "top_tracks"}), _message_stop()],
            [_text_delta("done"), _message_stop()],
        )
        registry = _mock_registry({
            "listening_stats": ToolResult(success=True, data=[{"track_name": "505"}], chart_spec=chart)
        })
        loop = AgentLoop(llm=llm, tool_registry=registry)
        events = await _collect(loop, [])

        end = next(e for e in events if isinstance(e, ToolEndEvent))
        assert end.success is True
        assert end.data == [{"track_name": "505"}]
        assert end.chart_spec == chart
        assert end.error is None

    @pytest.mark.asyncio
    async def test_final_text_and_done_after_tool(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()],
            [_text_delta("Your total plays: 10."), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={"total_plays": 10})})
        loop = AgentLoop(llm=llm, tool_registry=registry)
        events = await _collect(loop, [])

        texts = [e for e in events if isinstance(e, TextChunkEvent)]
        done = [e for e in events if isinstance(e, DoneEvent)]
        assert texts[-1].text == "Your total plays: 10."
        assert len(done) == 1
        assert done[0].total_iterations == 2

    @pytest.mark.asyncio
    async def test_registry_execute_called_with_correct_args(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "totals", "limit": 5}), _message_stop()],
            [_text_delta("done"), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={})})
        loop = AgentLoop(llm=llm, tool_registry=registry)
        await _collect(loop, [])

        registry.execute.assert_awaited_once_with("listening_stats", {"metric": "totals", "limit": 5})


# ---------------------------------------------------------------------------
# Multiple tool calls in one iteration
# ---------------------------------------------------------------------------

class TestMultipleToolCallsOneIteration:
    @pytest.mark.asyncio
    async def test_two_tool_calls_both_executed(self):
        llm = _mock_llm(
            [
                _make_tool_use_event("c1", "listening_stats", {"metric": "totals"}),
                _make_tool_use_event("c2", "listening_stats", {"metric": "top_tracks"}),
                _message_stop(),
            ],
            [_text_delta("done"), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={})})
        loop = AgentLoop(llm=llm, tool_registry=registry)
        events = await _collect(loop, [])

        starts = [e for e in events if isinstance(e, ToolStartEvent)]
        ends = [e for e in events if isinstance(e, ToolEndEvent)]
        assert len(starts) == 2
        assert len(ends) == 2

    @pytest.mark.asyncio
    async def test_two_tool_call_ids_preserved(self):
        llm = _mock_llm(
            [
                _make_tool_use_event("c1", "listening_stats", {"metric": "totals"}),
                _make_tool_use_event("c2", "listening_stats", {"metric": "top_tracks"}),
                _message_stop(),
            ],
            [_text_delta("done"), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={})})
        loop = AgentLoop(llm=llm, tool_registry=registry)
        events = await _collect(loop, [])

        call_ids = [e.call_id for e in events if isinstance(e, ToolStartEvent)]
        assert set(call_ids) == {"c1", "c2"}


# ---------------------------------------------------------------------------
# Unknown tool name
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_produces_tool_end_with_error(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "nonexistent_tool", {}), _message_stop()],
            [_text_delta("I couldn't use that tool."), _message_stop()],
        )
        registry = _mock_registry()  # no tools registered by name
        loop = AgentLoop(llm=llm, tool_registry=registry)
        events = await _collect(loop, [])

        end = next(e for e in events if isinstance(e, ToolEndEvent))
        assert end.success is False
        assert end.error is not None

    @pytest.mark.asyncio
    async def test_unknown_tool_loop_continues_to_final_answer(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "nonexistent_tool", {}), _message_stop()],
            [_text_delta("fallback answer"), _message_stop()],
        )
        registry = _mock_registry()
        loop = AgentLoop(llm=llm, tool_registry=registry)
        events = await _collect(loop, [])

        texts = [e for e in events if isinstance(e, TextChunkEvent)]
        done = [e for e in events if isinstance(e, DoneEvent)]
        assert any("fallback answer" in e.text for e in texts)
        assert len(done) == 1


# ---------------------------------------------------------------------------
# Tool error result — loop continues
# ---------------------------------------------------------------------------

class TestToolErrorResult:
    @pytest.mark.asyncio
    async def test_tool_error_forwarded_in_end_event(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()],
            [_text_delta("Something went wrong."), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=False, error="DB timeout")})
        loop = AgentLoop(llm=llm, tool_registry=registry)
        events = await _collect(loop, [])

        end = next(e for e in events if isinstance(e, ToolEndEvent))
        assert end.success is False
        assert "DB timeout" in end.error

    @pytest.mark.asyncio
    async def test_tool_error_does_not_stop_loop(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()],
            [_text_delta("Answer despite error."), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=False, error="oops")})
        loop = AgentLoop(llm=llm, tool_registry=registry)
        events = await _collect(loop, [])

        done = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done) == 1


# ---------------------------------------------------------------------------
# Max iterations exhausted
# ---------------------------------------------------------------------------

class TestMaxIterations:
    @pytest.mark.asyncio
    async def test_error_event_when_max_iterations_hit(self):
        # Every iteration is a tool call; the LLM never produces a text-only turn.
        tool_iter = [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()]
        llm = _mock_llm(*[tool_iter for _ in range(3)])
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={})})
        loop = AgentLoop(llm=llm, tool_registry=registry, max_iterations=3)
        events = await _collect(loop, [])

        error_events = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(error_events) == 1

    @pytest.mark.asyncio
    async def test_error_message_mentions_max_iterations(self):
        tool_iter = [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()]
        llm = _mock_llm(*[tool_iter for _ in range(2)])
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={})})
        loop = AgentLoop(llm=llm, tool_registry=registry, max_iterations=2)
        events = await _collect(loop, [])

        err = next(e for e in events if isinstance(e, ErrorEvent))
        assert "max_iterations" in err.message or "2" in err.message

    @pytest.mark.asyncio
    async def test_no_done_event_when_max_iterations_hit(self):
        tool_iter = [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()]
        llm = _mock_llm(*[tool_iter for _ in range(2)])
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={})})
        loop = AgentLoop(llm=llm, tool_registry=registry, max_iterations=2)
        events = await _collect(loop, [])

        assert not any(isinstance(e, DoneEvent) for e in events)


# ---------------------------------------------------------------------------
# chart_spec forwarded
# ---------------------------------------------------------------------------

class TestChartSpecForwarded:
    @pytest.mark.asyncio
    async def test_chart_spec_on_tool_end_event(self):
        chart = {"chart_type": "bar", "x": ["A"], "y": [1], "title": "Top Tracks"}
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "top_tracks"}), _message_stop()],
            [_text_delta("Here are your top tracks."), _message_stop()],
        )
        registry = _mock_registry({
            "listening_stats": ToolResult(success=True, data=[{"track_name": "A"}], chart_spec=chart)
        })
        loop = AgentLoop(llm=llm, tool_registry=registry)
        events = await _collect(loop, [])

        end = next(e for e in events if isinstance(e, ToolEndEvent))
        assert end.chart_spec == chart

    @pytest.mark.asyncio
    async def test_no_chart_spec_when_tool_returns_none(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()],
            [_text_delta("totals"), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={}, chart_spec=None)})
        loop = AgentLoop(llm=llm, tool_registry=registry)
        events = await _collect(loop, [])

        end = next(e for e in events if isinstance(e, ToolEndEvent))
        assert end.chart_spec is None


# ---------------------------------------------------------------------------
# Audit callback (on_tool_call)
# ---------------------------------------------------------------------------

class TestAuditCallback:
    @pytest.mark.asyncio
    async def test_on_tool_call_invoked_with_correct_kwargs(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()],
            [_text_delta("done"), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={"total_plays": 5})})
        audit = AsyncMock()
        loop = AgentLoop(llm=llm, tool_registry=registry, on_tool_call=audit)
        await _collect(loop, [], session_id="sess-99")

        audit.assert_awaited_once()
        kwargs = audit.call_args.kwargs
        assert kwargs["session_id"] == "sess-99"
        assert kwargs["call_id"] == "c1"
        assert kwargs["tool_name"] == "listening_stats"
        assert kwargs["tool_input"] == {"metric": "totals"}
        assert isinstance(kwargs["result"], ToolResult)
        assert isinstance(kwargs["latency_ms"], int)
        assert kwargs["iteration"] == 1

    @pytest.mark.asyncio
    async def test_on_tool_call_exception_silently_ignored(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()],
            [_text_delta("done"), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={})})

        async def bad_audit(**_):
            raise RuntimeError("audit DB down")

        loop = AgentLoop(llm=llm, tool_registry=registry, on_tool_call=bad_audit)
        # Must not raise
        events = await _collect(loop, [])
        done = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done) == 1

    @pytest.mark.asyncio
    async def test_no_on_tool_call_wired_no_crash(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()],
            [_text_delta("done"), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={})})
        loop = AgentLoop(llm=llm, tool_registry=registry)  # on_tool_call=None
        events = await _collect(loop, [])
        assert any(isinstance(e, DoneEvent) for e in events)

    @pytest.mark.asyncio
    async def test_on_tool_call_invoked_once_per_tool_call(self):
        llm = _mock_llm(
            [
                _make_tool_use_event("c1", "listening_stats", {"metric": "totals"}),
                _make_tool_use_event("c2", "listening_stats", {"metric": "top_tracks"}),
                _message_stop(),
            ],
            [_text_delta("done"), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={})})
        audit = AsyncMock()
        loop = AgentLoop(llm=llm, tool_registry=registry, on_tool_call=audit)
        await _collect(loop, [])

        assert audit.await_count == 2


# ---------------------------------------------------------------------------
# Caller message list not mutated
# ---------------------------------------------------------------------------

class TestMessageListImmutability:
    @pytest.mark.asyncio
    async def test_caller_messages_not_mutated(self):
        llm = _mock_llm(
            [_make_tool_use_event("c1", "listening_stats", {"metric": "totals"}), _message_stop()],
            [_text_delta("done"), _message_stop()],
        )
        registry = _mock_registry({"listening_stats": ToolResult(success=True, data={})})
        loop = AgentLoop(llm=llm, tool_registry=registry)

        original = [{"role": "user", "content": "What are my totals?"}]
        original_copy = list(original)
        await _collect(loop, original)

        assert original == original_copy


# ---------------------------------------------------------------------------
# _serialise helper (edge cases)
# ---------------------------------------------------------------------------

class TestSerialise:
    def test_none_returns_empty_string(self):
        from app.agent.loop import _serialise
        assert _serialise(None) == ""

    def test_string_passthrough(self):
        from app.agent.loop import _serialise
        assert _serialise("hello") == "hello"

    def test_dict_serialised_as_json(self):
        from app.agent.loop import _serialise
        result = _serialise({"key": "val"})
        assert json.loads(result) == {"key": "val"}

    def test_list_of_dicts_serialised(self):
        from app.agent.loop import _serialise
        data = [{"a": 1}, {"b": 2}]
        assert json.loads(_serialise(data)) == data

    def test_non_serialisable_falls_back_to_str(self):
        from app.agent.loop import _serialise

        class Obj:
            def __str__(self):
                return "custom"

        # json.dumps with default=str handles this, so it won't fall to str()
        result = _serialise({"o": Obj()})
        assert "custom" in result
