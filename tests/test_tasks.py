import uuid
from unittest.mock import MagicMock, patch

import numpy as np
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


@patch("worker.tasks.ml_inference._load_model")
@patch("worker.tasks.ml_inference.get_sync_db")
def test_ml_inference_task(mock_get_db, mock_load_model):
    job_id = str(uuid.uuid4())
    mock_job = _make_job(job_id, {"seed_tracks": ["tid_a", "tid_b"], "top_n": 3})
    mock_db = MagicMock()
    mock_db.get.return_value = mock_job
    mock_get_db.return_value = mock_db

    track_ids = ["tid_a", "tid_b", "tid_c", "tid_d"]
    # tid_c scores high from both seeds; tid_d from tid_b only
    matrix = np.array(
        [
            [0.0, 0.5, 0.9, 0.2],
            [0.5, 0.0, 0.3, 0.8],
            [0.9, 0.3, 0.0, 0.4],
            [0.2, 0.8, 0.4, 0.0],
        ],
        dtype=np.float32,
    )
    track_lookup = {
        "tid_a": {"track_name": "Track A", "artist": "Artist 1"},
        "tid_b": {"track_name": "Track B", "artist": "Artist 2"},
        "tid_c": {"track_name": "Track C", "artist": "Artist 3"},
        "tid_d": {"track_name": "Track D", "artist": "Artist 4"},
    }
    mock_load_model.return_value = {
        "matrix": matrix,
        "track_ids": track_ids,
        "track_id_to_idx": {t: i for i, t in enumerate(track_ids)},
        "track_lookup": track_lookup,
    }

    from worker.tasks.ml_inference import run_ml_inference

    run_ml_inference(job_id)

    assert mock_job.status == "complete"
    result = mock_job.result
    assert "recommendations" in result
    assert result["seed_count"] == 2
    assert result["model"] == "spotify_recommender"
    rec_ids = [r["track_id"] for r in result["recommendations"]]
    assert "tid_a" not in rec_ids
    assert "tid_b" not in rec_ids
    assert len(rec_ids) <= 3
    assert rec_ids[0] == "tid_c"  # highest combined score (0.9+0.3=1.2)
    for rec in result["recommendations"]:
        assert "track_id" in rec
        assert "score" in rec
    mock_db.commit.assert_called()
    mock_db.close.assert_called_once()


@patch("worker.tasks.ml_inference._load_model")
@patch("worker.tasks.ml_inference.get_sync_db")
def test_ml_inference_unknown_seeds_skipped(mock_get_db, mock_load_model):
    job_id = str(uuid.uuid4())
    mock_job = _make_job(job_id, {"seed_tracks": ["unknown_track"], "top_n": 5})
    mock_db = MagicMock()
    mock_db.get.return_value = mock_job
    mock_get_db.return_value = mock_db

    track_ids = ["tid_a", "tid_b"]
    matrix = np.array([[0.0, 0.7], [0.7, 0.0]], dtype=np.float32)
    mock_load_model.return_value = {
        "matrix": matrix,
        "track_ids": track_ids,
        "track_id_to_idx": {t: i for i, t in enumerate(track_ids)},
        "track_lookup": {},
    }

    from worker.tasks.ml_inference import run_ml_inference

    run_ml_inference(job_id)

    assert mock_job.status == "complete"
    assert mock_job.result["seed_count"] == 0
    assert mock_job.result["recommendations"] == []


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
