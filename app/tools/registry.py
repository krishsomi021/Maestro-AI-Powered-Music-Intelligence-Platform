"""
ToolRegistry — maps tool names to BaseTool instances and exposes the
.schemas list and .execute() method the agent loop depends on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.config import Settings


class ToolRegistry:
    def __init__(self, tools: list[BaseTool]) -> None:
        self._tools: dict[str, BaseTool] = {t.name: t for t in tools}

    @property
    def schemas(self) -> list[dict]:
        """Anthropic-shaped tool definitions — passed directly to llm.stream(tools=...)."""
        return [t.anthropic_schema for t in self._tools.values()]

    async def execute(self, name: str, input_dict: dict) -> ToolResult:
        """
        Dispatch to the named tool. Guarantees:
          - unknown name  → ToolResult(success=False)
          - any exception → ToolResult(success=False)
        A tool must never raise into the agent loop.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(success=False, error=f"Unknown tool: '{name}'")

        try:
            return await tool.execute(**input_dict)
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


def build_default_registry(db: "AsyncSession", settings: "Settings") -> ToolRegistry:
    """
    Constructs and registers all available tools for a request.
    RunSqlQueryTool owns its module-level readonly engine singleton — do not pass db.
    """
    from app.tools.listening_stats import ListeningStatsTool
    from app.tools.sql_query import RunSqlQueryTool
    from app.tools.semantic_search import SemanticTrackSearchTool
    from app.tools.recommendations import GetRecommendationsTool
    from app.tools.listening_profile import GetListeningProfileTool

    return ToolRegistry(
        tools=[
            ListeningStatsTool(db=db, settings=settings),
            RunSqlQueryTool(settings=settings),
            SemanticTrackSearchTool(db=db, settings=settings),
            GetRecommendationsTool(db=db, settings=settings),
            GetListeningProfileTool(db=db, settings=settings),
        ]
    )
