"""
Phase 2 tests — tool system foundation and listening_stats.

Uses the SAVEPOINT-rollback db_session fixture from conftest — no schema
mutation, no persistent data. Rows inserted in each test are rolled back.

top_genres tests use a mock session because track_metadata requires the
pgvector extension which is unavailable in the CI/sandbox environment.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.config import settings
from app.models.listening_history import ListeningHistory
from app.tools.base import BaseTool, ToolResult
from app.tools.listening_stats import ListeningStatsTool
from app.tools.registry import ToolRegistry, build_default_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool(db) -> ListeningStatsTool:
    return ListeningStatsTool(db=db, settings=settings)


async def _seed(db_session, rows: list[dict]) -> None:
    """Insert ListeningHistory rows and flush so the SQL tool can see them."""
    db_session.add_all([ListeningHistory(**r) for r in rows])
    await db_session.flush()


def _mock_db_rows(row_dicts: list[dict]) -> AsyncMock:
    """Return an AsyncMock db whose execute() returns the given row dicts."""
    mock_rows = []
    for d in row_dicts:
        r = MagicMock()
        r._mapping = d
        mock_rows.append(r)

    mock_result = MagicMock()
    mock_result.all.return_value = mock_rows

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result
    return mock_db


# ---------------------------------------------------------------------------
# ToolResult shape
# ---------------------------------------------------------------------------

class TestToolResult:
    def test_defaults(self):
        r = ToolResult(success=True)
        assert r.data is None
        assert r.error is None
        assert r.chart_spec is None

    def test_failure_shape(self):
        r = ToolResult(success=False, error="boom")
        assert not r.success
        assert r.error == "boom"
        assert r.chart_spec is None


# ---------------------------------------------------------------------------
# BaseTool / anthropic_schema shape
# ---------------------------------------------------------------------------

class TestBaseToolSchema:
    def test_anthropic_schema_shape(self):
        tool = _make_tool(_mock_db_rows([]))
        schema = tool.anthropic_schema
        assert schema["name"] == "listening_stats"
        assert "description" in schema
        assert schema["input_schema"]["type"] == "object"
        assert "metric" in schema["input_schema"]["properties"]
        assert "metric" in schema["input_schema"].get("required", [])


# ---------------------------------------------------------------------------
# top_tracks
# ---------------------------------------------------------------------------

class TestTopTracks:
    @pytest.mark.asyncio
    async def test_returns_success_and_chart_spec(self, db_session):
        await _seed(db_session, [
            {"artist_name": "Arctic Monkeys", "track_name": "505",
             "end_time": datetime(2025, 3, 1, 12, 0), "ms_played": 200_000},
            {"artist_name": "Arctic Monkeys", "track_name": "505",
             "end_time": datetime(2025, 3, 2, 12, 0), "ms_played": 210_000},
            {"artist_name": "Tame Impala", "track_name": "Elephant",
             "end_time": datetime(2025, 3, 3, 12, 0), "ms_played": 220_000},
        ])
        result = await _make_tool(db_session).execute(metric="top_tracks", limit=10)

        assert result.success
        assert result.error is None
        assert isinstance(result.data, list)
        assert result.chart_spec is not None
        assert result.chart_spec["chart_type"] == "bar"

    @pytest.mark.asyncio
    async def test_ranks_by_play_count(self, db_session):
        await _seed(db_session, [
            {"artist_name": "A", "track_name": "Song1",
             "end_time": datetime(2025, 4, 1, 10, 0), "ms_played": 180_000},
            {"artist_name": "A", "track_name": "Song1",
             "end_time": datetime(2025, 4, 2, 10, 0), "ms_played": 180_000},
            {"artist_name": "B", "track_name": "Song2",
             "end_time": datetime(2025, 4, 3, 10, 0), "ms_played": 180_000},
        ])
        result = await _make_tool(db_session).execute(
            metric="top_tracks", limit=10,
            date_from="2025-04-01", date_to="2025-04-30",
        )

        assert result.success
        top = result.data[0]
        assert top["track_name"] == "Song1"
        assert top["play_count"] == 2

    @pytest.mark.asyncio
    async def test_null_date_regression_guard(self, db_session):
        """No date params must still return rows — guards against ts >= NULL bug."""
        await _seed(db_session, [
            {"artist_name": "Radiohead", "track_name": "Creep",
             "end_time": datetime(2024, 6, 10, 9, 0), "ms_played": 230_000},
        ])
        result = await _make_tool(db_session).execute(metric="top_tracks")

        assert result.success
        assert len(result.data) >= 1


# ---------------------------------------------------------------------------
# top_artists
# ---------------------------------------------------------------------------

class TestTopArtists:
    @pytest.mark.asyncio
    async def test_returns_success_and_chart_spec(self, db_session):
        await _seed(db_session, [
            {"artist_name": "Blur", "track_name": "Song2",
             "end_time": datetime(2025, 5, 1, 10, 0), "ms_played": 200_000},
            {"artist_name": "Blur", "track_name": "Beetlebum",
             "end_time": datetime(2025, 5, 2, 10, 0), "ms_played": 190_000},
        ])
        result = await _make_tool(db_session).execute(metric="top_artists", limit=5)

        assert result.success
        assert result.chart_spec is not None
        assert result.chart_spec["chart_type"] == "bar"
        artists = [r["artist_name"] for r in result.data]
        assert "Blur" in artists


# ---------------------------------------------------------------------------
# top_genres (mocked — track_metadata requires pgvector, unavailable in sandbox)
# ---------------------------------------------------------------------------

class TestTopGenres:
    @pytest.mark.asyncio
    async def test_returns_success_and_chart_spec(self):
        mock_db = _mock_db_rows([
            {"genre": "indie rock", "play_count": 8},
            {"genre": "alternative", "play_count": 5},
        ])
        result = await ListeningStatsTool(db=mock_db, settings=settings).execute(
            metric="top_genres", limit=10
        )

        assert result.success
        assert result.error is None
        assert result.data == [
            {"genre": "indie rock", "play_count": 8},
            {"genre": "alternative", "play_count": 5},
        ]
        assert result.chart_spec is not None
        assert result.chart_spec["chart_type"] == "bar"
        assert result.chart_spec["x"] == ["indie rock", "alternative"]
        assert result.chart_spec["y"] == [8, 5]


# ---------------------------------------------------------------------------
# listening_by_hour — including local-tz test
# ---------------------------------------------------------------------------

class TestListeningByHour:
    @pytest.mark.asyncio
    async def test_returns_success_and_chart_spec(self, db_session):
        await _seed(db_session, [
            {"artist_name": "Joy Division", "track_name": "Love Will Tear Us Apart",
             "end_time": datetime(2099, 2, 1, 14, 0), "ms_played": 200_000},
        ])
        result = await _make_tool(db_session).execute(
            metric="listening_by_hour",
            date_from="2099-01-01", date_to="2099-12-31",
        )

        assert result.success
        assert result.chart_spec is not None
        assert result.chart_spec["chart_type"] == "bar"

    @pytest.mark.asyncio
    async def test_buckets_reflect_local_time_not_utc(self, db_session):
        """
        Seed a row at 2099-01-15 02:30 UTC.
        America/New_York in January is UTC-5, so local time is 21:30.
        Expect the row in hour-21 bucket, NOT hour-2.
        """
        await _seed(db_session, [
            {"artist_name": "Test Artist", "track_name": "Tz Test Track",
             "end_time": datetime(2099, 1, 15, 2, 30), "ms_played": 180_000},
        ])
        result = await _make_tool(db_session).execute(
            metric="listening_by_hour",
            date_from="2099-01-01", date_to="2099-12-31",
        )

        assert result.success
        hour_map = {row["hour"]: row["play_count"] for row in result.data}
        # Local hour must appear (21 = 02:30 UTC - 5h EST)
        assert 21 in hour_map, f"Expected local hour 21 in {hour_map}"
        # UTC hour must not appear as its own bucket for our isolated date range
        assert 2 not in hour_map, f"UTC hour 2 should not appear; got {hour_map}"


# ---------------------------------------------------------------------------
# listening_by_day_of_week
# ---------------------------------------------------------------------------

class TestListeningByDayOfWeek:
    @pytest.mark.asyncio
    async def test_returns_success_and_chart_spec(self, db_session):
        await _seed(db_session, [
            {"artist_name": "The Strokes", "track_name": "Last Nite",
             "end_time": datetime(2025, 6, 9, 12, 0), "ms_played": 200_000},   # Monday UTC
        ])
        result = await _make_tool(db_session).execute(metric="listening_by_day_of_week")

        assert result.success
        assert result.chart_spec is not None
        assert result.chart_spec["chart_type"] == "bar"
        # x-labels should be day abbreviations (Sun–Sat)
        for label in result.chart_spec["x"]:
            assert label in ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


# ---------------------------------------------------------------------------
# monthly_trends
# ---------------------------------------------------------------------------

class TestMonthlyTrends:
    @pytest.mark.asyncio
    async def test_returns_success_and_line_chart(self, db_session):
        await _seed(db_session, [
            {"artist_name": "Interpol", "track_name": "Obstacle 1",
             "end_time": datetime(2099, 3, 10, 12, 0), "ms_played": 220_000},
            {"artist_name": "Interpol", "track_name": "Obstacle 2",
             "end_time": datetime(2099, 4, 5, 12, 0), "ms_played": 215_000},
        ])
        result = await _make_tool(db_session).execute(
            metric="monthly_trends",
            date_from="2099-01-01", date_to="2099-12-31",
        )

        assert result.success
        assert result.chart_spec is not None
        assert result.chart_spec["chart_type"] == "line"
        months = result.chart_spec["x"]
        assert "2099-03" in months
        assert "2099-04" in months


# ---------------------------------------------------------------------------
# totals — no chart_spec
# ---------------------------------------------------------------------------

class TestTotals:
    @pytest.mark.asyncio
    async def test_returns_success_no_chart_spec(self, db_session):
        await _seed(db_session, [
            {"artist_name": "Pixies", "track_name": "Where Is My Mind",
             "end_time": datetime(2025, 7, 1, 10, 0), "ms_played": 230_000},
            {"artist_name": "Pixies", "track_name": "Here Comes Your Man",
             "end_time": datetime(2025, 7, 2, 10, 0), "ms_played": 190_000},
        ])
        result = await _make_tool(db_session).execute(metric="totals")

        assert result.success
        assert result.chart_spec is None
        assert "total_plays" in result.data
        assert "unique_artists" in result.data
        assert "unique_tracks" in result.data

    @pytest.mark.asyncio
    async def test_null_date_regression_guard(self, db_session):
        """totals with no date params returns a result row."""
        await _seed(db_session, [
            {"artist_name": "Placebo", "track_name": "Every You Every Me",
             "end_time": datetime(2024, 8, 1, 10, 0), "ms_played": 200_000},
        ])
        result = await _make_tool(db_session).execute(metric="totals")

        assert result.success
        assert result.data is not None
        assert result.data["total_plays"] >= 1


# ---------------------------------------------------------------------------
# ToolRegistry — exception-catching and unknown-name
# ---------------------------------------------------------------------------

class TestRegistry:
    @pytest.mark.asyncio
    async def test_unknown_tool_name_returns_failure(self):
        registry = ToolRegistry(tools=[])
        result = await registry.execute("nonexistent_tool", {})

        assert not result.success
        assert "nonexistent_tool" in result.error

    @pytest.mark.asyncio
    async def test_exception_in_tool_becomes_failure_not_raise(self):
        class BrokenTool(BaseTool):
            name = "broken"
            description = "always explodes"

            @property
            def input_schema(self):
                return {"type": "object", "properties": {}}

            async def execute(self, **kwargs) -> ToolResult:
                raise RuntimeError("internal explosion")

        registry = ToolRegistry(tools=[BrokenTool()])
        result = await registry.execute("broken", {})

        assert not result.success
        assert "internal explosion" in result.error

    def test_schemas_property_returns_anthropic_shape(self):
        mock_db = _mock_db_rows([])
        registry = build_default_registry(db=mock_db, settings=settings)
        schemas = registry.schemas

        assert isinstance(schemas, list)
        assert len(schemas) >= 1
        first = schemas[0]
        assert "name" in first
        assert "description" in first
        assert "input_schema" in first

    @pytest.mark.asyncio
    async def test_unknown_metric_returns_failure(self):
        mock_db = _mock_db_rows([])
        tool = ListeningStatsTool(db=mock_db, settings=settings)
        result = await tool.execute(metric="not_a_real_metric")

        assert not result.success
        assert "not_a_real_metric" in result.error
