"""
LLM provider abstraction.

LLMProvider is the single ABC used by the agent loop. It exposes one method:
stream() — every loop iteration goes through this, no non-streaming path exists.

StreamEvent union:
  text_delta     — a streamed text token
  tool_use       — a fully assembled tool_use block (surfaced once complete)
  message_stop   — end of the LLM turn, carries stop_reason + token usage
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal


# ---------------------------------------------------------------------------
# Stream event types
# ---------------------------------------------------------------------------

@dataclass
class TextDeltaEvent:
    type: Literal["text_delta"] = field(default="text_delta", init=False)
    text: str = ""


@dataclass
class ToolUseEvent:
    type: Literal["tool_use"] = field(default="tool_use", init=False)
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class MessageStopEvent:
    type: Literal["message_stop"] = field(default="message_stop", init=False)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


StreamEvent = TextDeltaEvent | ToolUseEvent | MessageStopEvent


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A fully assembled tool call from a single LLM iteration."""
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    """Non-streaming response wrapper (used in tests / evals, not the hot path)."""
    content: str
    tool_calls: list[ToolCall]
    stop_reason: str
    input_tokens: int
    output_tokens: int


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base for all LLM provider errors."""


class LLMRateLimitError(LLMError):
    """Provider rate-limit hit (429)."""


class LLMAPIError(LLMError):
    """Non-rate-limit API error from the provider."""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """
    Provider-agnostic LLM interface. The agent loop calls stream() for every
    iteration. Implementations must not add a separate complete() — keeping one
    mode guarantees the final answer is generated exactly once.
    """

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        max_tokens: int = 2048,
    ) -> AsyncIterator[StreamEvent]:
        """
        Stream one LLM turn. Yields:
          - TextDeltaEvent for each text token
          - ToolUseEvent once per complete tool_use block (assembled from deltas)
          - MessageStopEvent exactly once at the end of the turn
        Raises LLMRateLimitError or LLMAPIError on provider errors.
        """
        ...  # pragma: no cover
