"""
Unit tests for GetRecommendationsTool's discovery_mode branch. No real DB —
the AsyncSession is mocked with two sequential execute() results: the taste
centroid query, then the external_catalog ranking query.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.tools.recommendations import GetRecommendationsTool


def _row(**kwargs):
    r = MagicMock()
    for k, v in kwargs.items():
        setattr(r, k, v)
    return r


def _make_db(centroid_rows, candidate_rows):
    centroid_result = MagicMock()
    centroid_result.all.return_value = centroid_rows
    candidate_result = MagicMock()
    candidate_result.all.return_value = candidate_rows

    db = AsyncMock()
    db.execute.side_effect = [centroid_result, candidate_result]
    return db


def _fake_embedding_str(seed: int = 1) -> str:
    return str([0.1 * seed] * 384)


class TestDiscoveryModeNoHistory:
    @pytest.mark.asyncio
    async def test_no_listening_history_returns_failure(self):
        db = _make_db(centroid_rows=[], candidate_rows=[])
        tool = GetRecommendationsTool(db=db, settings=settings)
        result = await tool.execute(discovery_mode=True)
        assert result.success is False
        assert "taste profile" in result.error


class TestDiscoveryModeReturnsRankedResults:
    @pytest.mark.asyncio
    async def test_returns_expected_shape(self):
        centroid_rows = [_row(embedding=_fake_embedding_str(1)), _row(embedding=_fake_embedding_str(2))]
        candidate_rows = [
            _row(artist_name="Fever Ray", track_name="If I Had a Heart", lastfm_match_score=0.72, score=0.88),
            _row(artist_name="Portishead", track_name="Glory Box", lastfm_match_score=0.65, score=0.81),
        ]
        db = _make_db(centroid_rows, candidate_rows)
        tool = GetRecommendationsTool(db=db, settings=settings)

        result = await tool.execute(discovery_mode=True, limit=5)

        assert result.success is True
        recs = result.data["recommendations"]
        assert len(recs) == 2
        first = recs[0]
        assert first["artist_name"] == "Fever Ray"
        assert first["track_name"] == "If I Had a Heart"
        assert first["similarity_score"] == pytest.approx(0.88)
        assert first["lastfm_match_score"] == pytest.approx(0.72)

    @pytest.mark.asyncio
    async def test_discovery_mode_ignores_seed_track_names(self):
        centroid_rows = [_row(embedding=_fake_embedding_str(1))]
        candidate_rows = [
            _row(artist_name="X", track_name="Y", lastfm_match_score=0.5, score=0.5),
        ]
        db = _make_db(centroid_rows, candidate_rows)
        tool = GetRecommendationsTool(db=db, settings=settings)

        result = await tool.execute(seed_track_names=["Some Seed"], discovery_mode=True)

        assert result.success is True
        # Only two execute() calls were consumed (centroid + candidate ranking) —
        # confirms seed_track_names never triggered the closed-world lookup path.
        assert db.execute.call_count == 2


class TestNonDiscoveryModeUnaffected:
    @pytest.mark.asyncio
    async def test_default_still_requires_seeds(self):
        db = AsyncMock()
        tool = GetRecommendationsTool(db=db, settings=settings)
        result = await tool.execute(seed_track_names=None)
        assert result.success is False
        db.execute.assert_not_called()
