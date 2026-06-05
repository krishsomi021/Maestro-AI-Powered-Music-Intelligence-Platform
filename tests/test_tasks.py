import uuid
from unittest.mock import MagicMock, patch

import pytest


def _make_job(job_id, payload):
    job = MagicMock()
    job.id = uuid.UUID(job_id)
    job.payload = payload
    job.status = "pending"
    job.started_at = None
    job.completed_at = None
    job.result = None
    job.error = None
    return job


# ---------------------------------------------------------------------------
# ml_inference — recommender mode
# ---------------------------------------------------------------------------

@patch("worker.tasks.ml_inference._load_recommender_model")
@patch("worker.tasks.ml_inference.get_sync_db")
def test_spotify_recommender_task(mock_get_db, mock_load_model):
    seed_uri = "spotify:track:seed1"
    rec_uri_a = "spotify:track:rec_a"
    rec_uri_b = "spotify:track:rec_b"
    other_seed = "spotify:track:seed2"

    mock_model = {
        "similarity": {
            seed_uri: {rec_uri_a: 0.8, rec_uri_b: 0.3, other_seed: 0.5},
            other_seed: {rec_uri_a: 0.6, rec_uri_b: 0.9, seed_uri: 0.5},
        },
        "uri_to_meta": {
            seed_uri: {"track_name": "Seed Song", "artist_name": "Artist A", "uri": seed_uri},
            other_seed: {"track_name": "Seed Two", "artist_name": "Artist B", "uri": other_seed},
            rec_uri_a: {"track_name": "Rec A", "artist_name": "Artist C", "uri": rec_uri_a},
            rec_uri_b: {"track_name": "Rec B", "artist_name": "Artist D", "uri": rec_uri_b},
        },
        "saved_tracks": set(),
        "trained_at": "2026-01-01T00:00:00",
        "total_tracks": 4,
        "total_plays": 200,
    }
    mock_load_model.return_value = mock_model

    job_id = str(uuid.uuid4())
    mock_job = _make_job(job_id, {"seed_tracks": [seed_uri, other_seed], "top_n": 5})
    mock_db = MagicMock()
    mock_db.get.return_value = mock_job
    mock_get_db.return_value = mock_db

    from worker.tasks.ml_inference import run_ml_inference

    run_ml_inference(job_id)

    assert mock_job.status == "complete"
    result = mock_job.result

    # Required top-level fields
    assert result["model"] == "spotify_recommender"
    assert result["seed_count"] == 2
    assert result["top_n"] == 5
    assert "recommendations" in result

    recs = result["recommendations"]
    assert len(recs) <= 5

    # Seeds must not appear in results
    rec_uris = [r["uri"] for r in recs]
    assert seed_uri not in rec_uris
    assert other_seed not in rec_uris

    # rec_uri_a scores 0.8+0.6=1.4; rec_uri_b scores 0.3+0.9=1.2 — rec_a must be first
    assert recs[0]["uri"] == rec_uri_a

    # Each recommendation must have the required fields
    for rec in recs:
        assert "uri" in rec
        assert "track_name" in rec
        assert "artist_name" in rec
        assert "score" in rec

    mock_db.commit.assert_called()
    mock_db.close.assert_called_once()


@patch("worker.tasks.ml_inference._load_recommender_model")
@patch("worker.tasks.ml_inference.get_sync_db")
def test_spotify_recommender_unknown_seeds_returns_empty(mock_get_db, mock_load_model):
    """Seeds not found in the model are skipped; zero found seeds -> empty recommendations."""
    mock_load_model.return_value = {
        "similarity": {"spotify:track:known": {"spotify:track:other": 0.9}},
        "uri_to_meta": {},
        "saved_tracks": set(),
        "trained_at": "2026-01-01T00:00:00",
        "total_tracks": 2,
        "total_plays": 50,
    }

    job_id = str(uuid.uuid4())
    mock_job = _make_job(job_id, {"seed_tracks": ["spotify:track:unknown"], "top_n": 5})
    mock_db = MagicMock()
    mock_db.get.return_value = mock_job
    mock_get_db.return_value = mock_db

    from worker.tasks.ml_inference import run_ml_inference

    run_ml_inference(job_id)

    assert mock_job.status == "complete"
    assert mock_job.result["seed_count"] == 0
    assert mock_job.result["recommendations"] == []
    mock_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# Other worker tasks (unchanged)
# ---------------------------------------------------------------------------

@patch("worker.tasks.etl_pipeline.get_sync_db")
@patch("worker.tasks.etl_pipeline.time.sleep")
def test_etl_pipeline_task(mock_sleep, mock_get_db):
    job_id = str(uuid.uuid4())
    mock_job = _make_job(job_id, {"source": "raw", "destination": "clean"})
    mock_db = MagicMock()
    mock_db.get.return_value = mock_job
    mock_get_db.return_value = mock_db

    from worker.tasks.etl_pipeline import run_etl_pipeline

    run_etl_pipeline(job_id)

    assert mock_job.status == "complete"
    assert mock_job.result is not None
    assert "rows_processed" in mock_job.result
    mock_db.commit.assert_called()
    mock_db.close.assert_called_once()


@patch("worker.tasks.report_generation.get_sync_db")
@patch("worker.tasks.report_generation.time.sleep")
def test_report_generation_task(mock_sleep, mock_get_db):
    job_id = str(uuid.uuid4())
    mock_job = _make_job(job_id, {"report_type": "monthly_summary", "date_range": {}})
    mock_db = MagicMock()
    mock_db.get.return_value = mock_job
    mock_get_db.return_value = mock_db

    from worker.tasks.report_generation import run_report_generation

    run_report_generation(job_id)

    assert mock_job.status == "complete"
    assert mock_job.result is not None
    assert "report_id" in mock_job.result
    mock_db.commit.assert_called()
    mock_db.close.assert_called_once()
