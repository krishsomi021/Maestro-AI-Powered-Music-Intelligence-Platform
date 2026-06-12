"""
Tool system foundation — BaseTool ABC and ToolResult.

ToolResult fields match exactly what app/agent/loop.py reads:
  result.success, result.data, result.error, result.chart_spec
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str | None = None
    chart_spec: dict[str, Any] | None = None


class BaseTool(ABC):
    """
    All agent tools implement this ABC. No FastAPI imports — tools are
    unit-testable without the web layer.
    """

    name: str
    description: str

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """JSON Schema object describing the tool's parameters."""
        ...

    @property
    def anthropic_schema(self) -> dict[str, Any]:
        """
        Returns the tool definition dict in the exact shape the Anthropic
        tools API expects: {"name", "description", "input_schema"}.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """
        Run the tool. Must never raise — catch internally and return
        ToolResult(success=False, error=...) on failure.
        """
        ...
