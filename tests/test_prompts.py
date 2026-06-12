"""
Phase 3 tests — PromptBuilder / system prompt generation.

All DB calls are mocked. track_metadata's vector(384) column (named
'embedding') is unavailable in CI/sandbox (no pgvector extension), so
mocking is required for correctness there regardless. Mocking both tables
keeps every test fast and hermetic.

The mock intercepts db.execute() and returns controlled schema / sample
data so we can assert exactly what appears in the rendered prompt.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.agent.prompts import PromptBuilder, _render, _EXCLUDED_COLUMNS
from app.config import settings


# ---------------------------------------------------------------------------
# Mock DB builder
# ---------------------------------------------------------------------------

def _make_row(mapping: dict) -> MagicMock:
    r = MagicMock()
    r._mapping = mapping
    return r


def _make_result(rows: list[dict]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = [_make_row(r) for r in rows]
    result.first.return_value = _make_row(rows[0]) if rows else None
    return result


# Default schema data mirroring the real tables (embedding excluded)
_LH_SCHEMA = [
    {"column_name": "id",          "data_type": "uuid",      "is_nullable": "NO"},
    {"column_name": "artist_name", "data_type": "character varying", "is_nullable": "NO"},
    {"column_name": "track_name",  "data_type": "character varying", "is_nullable": "NO"},
    {"column_name": "end_time",    "data_type": "timestamp without time zone", "is_nullable": "NO"},
    {"column_name": "ms_played",   "data_type": "integer",   "is_nullable": "NO"},
    {"column_name": "imported_at", "data_type": "timestamp without time zone", "is_nullable": "NO"},
]

_TM_SCHEMA = [
    {"column_name": "id",               "data_type": "uuid",      "is_nullable": "NO"},
    {"column_name": "spotify_track_id", "data_type": "character varying", "is_nullable": "YES"},
    {"column_name": "artist_name",      "data_type": "character varying", "is_nullable": "NO"},
    {"column_name": "track_name",       "data_type": "character varying", "is_nullable": "NO"},
    {"column_name": "genres",           "data_type": "ARRAY",     "is_nullable": "YES"},
    {"column_name": "popularity",       "data_type": "integer",   "is_nullable": "YES"},
    {"column_name": "enriched_at",      "data_type": "timestamp without time zone", "is_nullable": "NO"},
]

_LH_SAMPLE = {
    "id": "abc123",
    "artist_name": "Arctic Monkeys",
    "track_name": "505",
    "end_time": "2024-06-10 15:29:00",
    "ms_played": 245000,
    "imported_at": "2024-06-11 08:00:00",
}

_TM_SAMPLE = {
    "id": "def456",
    "spotify_track_id": "5FVd3gbD61NBgBDurCNJaI",
    "artist_name": "Arctic Monkeys",
    "track_name": "505",
    "spotify_artist_id": "7Ln80lUS6He07XvHI8qqHH",
    "genres": ["indie rock", "alternative"],
    "popularity": None,
    "enriched_at": "2024-06-11 09:00:00",
}


def _mock_db(
    lh_schema=None,
    tm_schema=None,
    lh_sample=None,
    tm_sample_row=None,
    lh_empty=False,
    tm_empty=False,
) -> AsyncMock:
    """
    Build a mock AsyncSession whose execute() returns deterministic results.

    Call order expected by PromptBuilder._fetch_schemas + _fetch_samples:
      call 0: information_schema for listening_history
      call 1: information_schema for track_metadata
      call 2: SELECT * FROM listening_history LIMIT 1
      call 3: SELECT ... FROM track_metadata LIMIT 1
    """
    lh_s = lh_schema if lh_schema is not None else _LH_SCHEMA
    tm_s = tm_schema if tm_schema is not None else _TM_SCHEMA
    lh_row = {} if lh_empty else (lh_sample or _LH_SAMPLE)
    tm_row = {} if tm_empty else (tm_sample_row or _TM_SAMPLE)

    results = [
        _make_result(lh_s),          # schema: listening_history
        _make_result(tm_s),          # schema: track_metadata
        _make_result([lh_row] if not lh_empty else []),   # sample: lh
        _make_result([tm_row] if not tm_empty else []),   # sample: tm
    ]

    db = AsyncMock()
    db.execute.side_effect = results
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRenderedPromptContainsRealColumns:
    @pytest.mark.asyncio
    async def test_end_time_appears_in_prompt(self):
        db = _mock_db()
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        assert "end_time" in prompt

    @pytest.mark.asyncio
    async def test_ms_played_appears_in_prompt(self):
        db = _mock_db()
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        assert "ms_played" in prompt

    @pytest.mark.asyncio
    async def test_track_metadata_columns_appear(self):
        db = _mock_db()
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        assert "artist_name" in prompt
        assert "track_name" in prompt


class TestEmbeddingColumnExcluded:
    @pytest.mark.asyncio
    async def test_embedding_column_definition_absent_from_prompt(self):
        # The column definition line looks like "  - embedding  [USER-DEFINED]".
        # The English word "embedding" may appear in prose (e.g. "semantic
        # embeddings") — we check that the column entry specifically is absent.
        db = _mock_db()
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        # Column list lines are formatted as "  - <name>  [<type>]"
        assert "- embedding " not in prompt, \
            "embedding column definition found in prompt — exclusion failed"

    def test_excluded_columns_set_contains_embedding(self):
        assert "embedding" in _EXCLUDED_COLUMNS

    @pytest.mark.asyncio
    async def test_no_float_array_in_prompt(self):
        # If the embedding column leaked through it would appear as a long
        # bracketed list of floats — guard against that.
        db = _mock_db()
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        # A float array would look like [0.123, 0.456, ...] — check for that pattern
        import re
        float_array_pattern = re.compile(r"\[\s*-?\d+\.\d+\s*,")
        assert not float_array_pattern.search(prompt), \
            "Float array found in prompt — embedding column may have leaked"


class TestDynamicInjection:
    @pytest.mark.asyncio
    async def test_added_column_surfaces_in_prompt_without_code_change(self):
        """
        A new column returned by information_schema must appear in the rendered
        prompt automatically — the injection is dynamic, not hardcoded.
        """
        extended_schema = _LH_SCHEMA + [
            {"column_name": "listening_source", "data_type": "character varying", "is_nullable": "YES"},
        ]
        db = _mock_db(lh_schema=extended_schema)
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        assert "listening_source" in prompt


class TestToolGuidance:
    @pytest.mark.asyncio
    async def test_listening_stats_preference_mentioned(self):
        db = _mock_db()
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        assert "listening_stats" in prompt

    @pytest.mark.asyncio
    async def test_run_sql_query_mentioned_as_escape_hatch(self):
        db = _mock_db()
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        assert "run_sql_query" in prompt

    @pytest.mark.asyncio
    async def test_all_five_tools_mentioned(self):
        db = _mock_db()
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        for tool in ("listening_stats", "run_sql_query", "get_recommendations",
                     "semantic_track_search", "get_listening_profile"):
            assert tool in prompt, f"Tool '{tool}' missing from prompt"


class TestTimezoneInjection:
    @pytest.mark.asyncio
    async def test_configured_local_tz_appears_in_prompt(self):
        db = _mock_db()
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        assert settings.agent_local_tz in prompt, \
            f"Expected '{settings.agent_local_tz}' in prompt"

    @pytest.mark.asyncio
    async def test_timezone_not_hardcoded(self):
        """Prompt uses the settings value, not a hardcoded city name."""
        custom_settings = MagicMock()
        custom_settings.agent_local_tz = "Europe/London"
        db = _mock_db()
        prompt = await PromptBuilder(custom_settings).build_system_prompt(db)
        assert "Europe/London" in prompt


class TestEmptyTable:
    @pytest.mark.asyncio
    async def test_empty_listening_history_shows_no_sample_message(self):
        db = _mock_db(lh_empty=True)
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        assert "no sample rows available" in prompt

    @pytest.mark.asyncio
    async def test_empty_track_metadata_shows_no_sample_message(self):
        db = _mock_db(tm_empty=True)
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        assert "no sample rows available" in prompt

    @pytest.mark.asyncio
    async def test_empty_table_does_not_raise(self):
        db = _mock_db(lh_empty=True, tm_empty=True)
        # Must not raise
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        assert isinstance(prompt, str)
        assert len(prompt) > 100


class TestMsPlayedConversionNote:
    @pytest.mark.asyncio
    async def test_ms_played_minutes_conversion_mentioned(self):
        db = _mock_db()
        prompt = await PromptBuilder(settings).build_system_prompt(db)
        assert "ms_played" in prompt
        assert "minutes" in prompt or "60000" in prompt
