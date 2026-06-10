"""
Tests for the pgvector content-based recommender mode.
Never hits the database or Spotify API — all DB calls are mocked.
"""
import numpy as np
from unittest.mock import MagicMock, patch


def _make_db_with_seeds(seed_rows=None, candidate_rows=None):
    """
    Build a mock Session that returns seed_rows on the first execute()
    and candidate_rows on the second.
    """
    db = MagicMock()
    first = MagicMock()
    first.fetchall.return_value = seed_rows or []
    second = MagicMock()
    second.fetchall.return_value = candidate_rows or []
    db.execute.side_effect = [first, second]
    return db


def _fake_embedding(seed=42) -> list[float]:
    rng = np.random.default_rng(seed)
    return rng.random(384).tolist()


class TestPgvectorReturnsRankedResults:
    def test_returns_expected_shape(self):
        from worker.tasks.ml_inference import _run_pgvector_recommender

        seed_rows = [("aaa", _fake_embedding(1))]
        # row[3] is the score column (1 - cosine_distance), already computed in SQL
        candidate_rows = [
            ("Arctic Monkeys", "505", "bbb", 0.85),
            ("Radiohead", "Creep", "ccc", 0.78),
        ]
        db = _make_db_with_seeds(seed_rows, candidate_rows)

        payload = {"seed_tracks": ["spotify:track:aaa"], "top_n": 5, "recommender": "pgvector"}
        result = _run_pgvector_recommender(payload, db)

        assert result["model"] == "pgvector_recommender"
        assert result["seed_count"] == 1
        assert result["top_n"] == 5
        assert len(result["recommendations"]) == 2
        first_rec = result["recommendations"][0]
        assert first_rec["track_name"] == "505"
        assert first_rec["artist_name"] == "Arctic Monkeys"
        assert first_rec["uri"] == "spotify:track:bbb"
        assert abs(first_rec["score"] - 0.85) < 1e-4


class TestPgvectorMissingSeedsReturnsEmpty:
    def test_all_seeds_missing_returns_empty(self):
        from worker.tasks.ml_inference import _run_pgvector_recommender

        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []

        payload = {"seed_tracks": ["spotify:track:missing1", "spotify:track:missing2"], "top_n": 10}
        result = _run_pgvector_recommender(payload, db)

        assert result["recommendations"] == []
        assert result["seed_count"] == 0
        assert result["model"] == "pgvector_recommender"


class TestPayloadRouting:
    def test_pgvector_payload_routes_to_pgvector(self):
        from worker.tasks import ml_inference

        with patch.object(ml_inference, "_run_pgvector_recommender", return_value={"model": "pgvector_recommender"}) as mock_pgv, \
             patch.object(ml_inference, "_run_recommender") as mock_co:
            payload = {"seed_tracks": ["spotify:track:x"], "recommender": "pgvector"}
            db = MagicMock()

            if "features" in payload:
                ml_inference._run_fraud_detection(payload)
            elif payload.get("recommender") == "pgvector":
                ml_inference._run_pgvector_recommender(payload, db)
            else:
                ml_inference._run_recommender(payload)

            mock_pgv.assert_called_once_with(payload, db)
            mock_co.assert_not_called()

    def test_default_payload_routes_to_cooccurrence(self):
        from worker.tasks import ml_inference

        with patch.object(ml_inference, "_run_pgvector_recommender") as mock_pgv, \
             patch.object(ml_inference, "_run_recommender", return_value={"model": "spotify_recommender"}) as mock_co:
            payload = {"seed_tracks": ["spotify:track:x"]}
            db = MagicMock()

            if "features" in payload:
                ml_inference._run_fraud_detection(payload)
            elif payload.get("recommender") == "pgvector":
                ml_inference._run_pgvector_recommender(payload, db)
            else:
                ml_inference._run_recommender(payload)

            mock_co.assert_called_once_with(payload)
            mock_pgv.assert_not_called()


class TestUnparseableUriSkipped:
    def test_bad_uri_skipped_gracefully(self):
        from worker.tasks.ml_inference import _run_pgvector_recommender

        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []

        payload = {"seed_tracks": ["not-a-spotify-uri"], "top_n": 5}
        result = _run_pgvector_recommender(payload, db)

        assert result["recommendations"] == []
        assert result["seed_count"] == 0
