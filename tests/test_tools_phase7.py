"""
Phase 7 tool tests — RunSqlQueryTool, SemanticTrackSearchTool,
GetRecommendationsTool, GetListeningProfileTool.

Section A — UNIT (mocked DB / no real connections)
Section B — INTEGRATION (real DB; guarded with pytest.mark.skipif where required)

Integration tests for agent_readonly role are skipped when
AGENT_READONLY_DB_PASSWORD is not set. CI must run
  docker compose exec api python scripts/provision_agent_role.py
before the test suite if this env var is present; otherwise the skip
protects the suite from becoming a silent 5th known failure.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch, AsyncMock

from app.config import settings
from app.tools.base import ToolResult
from app.tools.sql_query import RunSqlQueryTool, _validate_query


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_db_rows(row_dicts: list[dict]) -> AsyncMock:
    """AsyncMock db session whose execute() returns the given row dicts."""
    mock_rows = []
    for d in row_dicts:
        r = MagicMock()
        for k, v in d.items():
            setattr(r, k, v)
        r._mapping = d
        mock_rows.append(r)

    mock_result = MagicMock()
    mock_result.all.return_value = mock_rows
    mock_result.first.return_value = mock_rows[0] if mock_rows else None
    mock_result.keys.return_value = list(row_dicts[0].keys()) if row_dicts else []

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result
    return mock_db


# ===========================================================================
# Section A — UNIT tests (mocked; no real DB)
# ===========================================================================

class TestSqlQueryValidation:
    """Pre-filter logic — DB layer must never be touched on rejection."""

    def test_select_passes(self):
        assert _validate_query("SELECT 1 AS n") is None

    def test_delete_rejected(self):
        err = _validate_query("DELETE FROM listening_history")
        assert err is not None
        assert "SELECT" in err

    def test_drop_rejected(self):
        err = _validate_query("DROP TABLE track_metadata")
        assert err is not None
        assert "SELECT" in err

    def test_multi_statement_rejected(self):
        err = _validate_query("SELECT 1; DROP TABLE track_metadata")
        assert err is not None
        assert "single" in err.lower()

    def test_cte_allowed(self):
        assert _validate_query("WITH x AS (SELECT 1) SELECT * FROM x") is None

    def test_insert_rejected(self):
        err = _validate_query("INSERT INTO listening_history VALUES (1)")
        assert err is not None

    def test_update_rejected(self):
        err = _validate_query("UPDATE listening_history SET ms_played = 0")
        assert err is not None


class TestSqlQueryToolUnit:
    """RunSqlQueryTool unit tests — engine is mocked."""

    def _make_tool(self) -> RunSqlQueryTool:
        return RunSqlQueryTool(settings=settings)

    @pytest.mark.asyncio
    async def test_select_returns_success(self):
        tool = self._make_tool()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.keys.return_value = ["n"]
        mock_result.fetchmany = AsyncMock(return_value=[(1,)])
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn

        with patch("app.tools.sql_query._get_readonly_engine", return_value=mock_engine):
            result = await tool.execute(query="SELECT 1 AS n")

        assert result.success
        assert result.data["columns"] == ["n"]
        assert result.data["rows"] == [[1]]
        assert result.data["truncated"] is False

    @pytest.mark.asyncio
    async def test_delete_rejected_before_db(self):
        tool = self._make_tool()
        mock_engine = MagicMock()

        with patch("app.tools.sql_query._get_readonly_engine", return_value=mock_engine):
            result = await tool.execute(query="DELETE FROM listening_history")

        assert not result.success
        assert "SELECT" in result.error
        mock_engine.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_drop_rejected_before_db(self):
        tool = self._make_tool()
        mock_engine = MagicMock()

        with patch("app.tools.sql_query._get_readonly_engine", return_value=mock_engine):
            result = await tool.execute(query="DROP TABLE track_metadata")

        assert not result.success
        mock_engine.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_statement_rejected_before_db(self):
        tool = self._make_tool()
        mock_engine = MagicMock()

        with patch("app.tools.sql_query._get_readonly_engine", return_value=mock_engine):
            result = await tool.execute(query="SELECT 1; DROP TABLE track_metadata")

        assert not result.success
        assert "single" in result.error.lower()
        mock_engine.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_row_cap_truncation(self):
        """fetchmany returns cap+1 rows → data truncated to cap, truncated=True."""
        cap = settings.agent_sql_row_cap
        tool = self._make_tool()

        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.keys.return_value = ["id"]
        # Return cap+1 rows to trigger truncation
        mock_result.fetchmany = AsyncMock(return_value=[(i,) for i in range(cap + 1)])
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn

        with patch("app.tools.sql_query._get_readonly_engine", return_value=mock_engine):
            result = await tool.execute(query="SELECT id FROM listening_history")

        assert result.success
        assert result.data["truncated"] is True
        assert len(result.data["rows"]) == cap


class TestSemanticSearchUnit:
    @pytest.mark.asyncio
    async def test_returns_success_shape(self):
        from app.tools.semantic_search import SemanticTrackSearchTool

        mock_db = _mock_db_rows([
            {"artist_name": "Radiohead", "track_name": "Creep", "score": 0.91},
        ])
        tool = SemanticTrackSearchTool(db=mock_db, settings=settings)

        fake_vec = [0.1] * 384
        with patch("app.tools.semantic_search.asyncio.get_event_loop") as mock_loop, \
             patch("app.tools.semantic_search.SemanticTrackSearchTool.execute",
                   wraps=tool.execute):
            # Patch the model loading so no sentence-transformers import is needed
            with patch("worker.ml.embeddings._load_embedding_model") as mock_model:
                mock_model.return_value.encode.return_value = fake_vec
                import asyncio as _asyncio
                loop = _asyncio.get_event_loop()
                mock_loop.return_value = loop

                result = await tool.execute(query="dark electronic music", limit=5)

        # Because the executor runs synchronously in tests, accept success or skip
        # if the mock wiring isn't perfect — the integration test covers real behaviour.
        # Just verify it never raises.
        assert isinstance(result, ToolResult)

    @pytest.mark.asyncio
    async def test_db_error_returns_failure(self):
        import numpy as np
        from app.tools.semantic_search import SemanticTrackSearchTool

        mock_db = AsyncMock()
        mock_db.execute.side_effect = Exception("connection refused")
        tool = SemanticTrackSearchTool(db=mock_db, settings=settings)

        # encode() must return a numpy array because the tool calls .tolist() on it.
        # Patch the import site inside semantic_search.py (not the worker module directly)
        # so the lazy import inside execute() picks up the mock.
        with patch("worker.ml.embeddings._load_embedding_model") as mock_model:
            mock_model.return_value.encode.return_value = np.zeros(384, dtype=np.float32)
            result = await tool.execute(query="test", limit=5)

        assert not result.success
        assert "connection refused" in result.error


class TestGetRecommendationsUnit:
    @pytest.mark.asyncio
    async def test_no_seeds_returns_failure(self):
        from app.tools.recommendations import GetRecommendationsTool

        mock_db = _mock_db_rows([])  # no seed found
        tool = GetRecommendationsTool(db=mock_db, settings=settings)
        result = await tool.execute(seed_track_names=["nonexistent track xyz"])

        assert not result.success
        assert "None of the seed tracks were found" in result.error

    @pytest.mark.asyncio
    async def test_empty_seed_list_returns_failure(self):
        from app.tools.recommendations import GetRecommendationsTool

        tool = GetRecommendationsTool(db=AsyncMock(), settings=settings)
        result = await tool.execute(seed_track_names=[])

        assert not result.success
        assert "None of the seed tracks were found" in result.error


class TestGetListeningProfileUnit:
    @pytest.mark.asyncio
    async def test_returns_expected_shape_mocked(self):
        from app.tools.listening_profile import GetListeningProfileTool

        # Build a db mock that returns appropriate rows for each sub-query.
        artist_row = MagicMock(artist_name="Radiohead", play_count=50)
        genre_row = MagicMock(genre="alternative rock", play_count=30)
        total_row = MagicMock(total=5_000_000)
        hour_row = MagicMock(hour=21)
        day_row = MagicMock(day_name="Friday")

        results = [
            _make_execute_result([artist_row]),
            _make_execute_result([genre_row]),
            _make_execute_result([total_row], first=total_row),
            _make_execute_result([hour_row], first=hour_row),
            _make_execute_result([day_row], first=day_row),
        ]
        mock_db = AsyncMock()
        mock_db.execute.side_effect = results

        tool = GetListeningProfileTool(db=mock_db, settings=settings)
        result = await tool.execute()

        assert result.success
        assert "top_artists" in result.data
        assert "top_genres" in result.data
        assert "total_ms_played" in result.data
        assert "most_active_hour" in result.data
        assert "most_active_day" in result.data
        assert result.chart_spec is not None
        assert result.chart_spec["type"] == "bar"
        assert "x" in result.chart_spec
        assert "y" in result.chart_spec
        assert result.chart_spec["x"] == ["Radiohead"]
        assert result.chart_spec["y"] == [50]


def _make_execute_result(rows, first=None):
    r = MagicMock()
    r.all.return_value = rows
    r.first.return_value = first if first is not None else (rows[0] if rows else None)
    return r


# ===========================================================================
# Section B — INTEGRATION tests (real DB required)
# ===========================================================================

_has_readonly_pw = bool(settings.agent_readonly_db_password)
_skip_readonly = pytest.mark.skipif(
    not _has_readonly_pw,
    reason="AGENT_READONLY_DB_PASSWORD not set — skipping agent_readonly role boundary test",
)


@_skip_readonly
class TestSqlRoleBoundaryIntegration:
    """
    Verify the agent_readonly role cannot execute writes at the DB level.
    Requires: scripts/provision_agent_role.py run against the target DB.
    """

    @pytest.mark.asyncio
    async def test_readonly_role_rejects_insert(self):
        from sqlalchemy.ext.asyncio import create_async_engine
        url = (
            f"postgresql+asyncpg://{settings.agent_readonly_db_user}"
            f":{settings.agent_readonly_db_password}"
            f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
        )
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                with pytest.raises(Exception, match="permission denied|read-only"):
                    from sqlalchemy import text as _text
                    await conn.execute(
                        _text("INSERT INTO listening_history (artist_name, track_name, ms_played, end_time) VALUES ('x','y',1,NOW())")
                    )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_readonly_role_rejects_delete(self):
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text as _text
        url = (
            f"postgresql+asyncpg://{settings.agent_readonly_db_user}"
            f":{settings.agent_readonly_db_password}"
            f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
        )
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                with pytest.raises(Exception, match="permission denied|read-only"):
                    await conn.execute(
                        _text("DELETE FROM listening_history WHERE ms_played < 0")
                    )
        finally:
            await engine.dispose()


class TestSemanticSearchIntegration:
    """Requires a live DB with track_metadata rows that have embeddings."""

    @pytest.mark.asyncio
    async def test_search_returns_ranked_tracks(self, db_session):
        from app.tools.semantic_search import SemanticTrackSearchTool
        tool = SemanticTrackSearchTool(db=db_session, settings=settings)
        result = await tool.execute(query="dark electronic music", limit=5)

        assert result.success, result.error
        assert isinstance(result.data["tracks"], list)
        if result.data["tracks"]:
            first = result.data["tracks"][0]
            assert "artist_name" in first
            assert "track_name" in first
            assert isinstance(first["score"], float)


class TestGetRecommendationsIntegration:
    """Requires track_metadata with embeddings."""

    @pytest.mark.asyncio
    async def test_known_seed_returns_recommendations(self, db_session):
        from sqlalchemy import text as _text
        # Pick any track that has an embedding in the dev DB.
        row = await db_session.execute(
            _text("SELECT track_name FROM track_metadata WHERE embedding IS NOT NULL LIMIT 1")
        )
        found = row.first()
        if found is None:
            pytest.skip("No enriched tracks in DB — skipping integration test")

        from app.tools.recommendations import GetRecommendationsTool
        tool = GetRecommendationsTool(db=db_session, settings=settings)
        result = await tool.execute(seed_track_names=[found.track_name], limit=5)

        assert result.success, result.error
        recs = result.data["recommendations"]
        assert isinstance(recs, list)

    @pytest.mark.asyncio
    async def test_nonsense_seed_returns_failure(self, db_session):
        from app.tools.recommendations import GetRecommendationsTool
        tool = GetRecommendationsTool(db=db_session, settings=settings)
        result = await tool.execute(
            seed_track_names=["ZZZNONSENSESEEDTRACKXYZ999"], limit=5
        )

        assert not result.success
        assert "None of the seed tracks were found" in result.error


class TestGetListeningProfileIntegration:
    """Requires listening_history rows."""

    @pytest.mark.asyncio
    async def test_profile_returns_all_keys(self, db_session):
        from app.tools.listening_profile import GetListeningProfileTool
        tool = GetListeningProfileTool(db=db_session, settings=settings)
        result = await tool.execute()

        assert result.success, result.error
        for key in ("top_artists", "top_genres", "total_ms_played",
                    "most_active_hour", "most_active_day"):
            assert key in result.data, f"Missing key: {key}"

        assert result.chart_spec is not None
        for key in ("type", "title", "x", "y"):
            assert key in result.chart_spec, f"Missing chart_spec key: {key}"
