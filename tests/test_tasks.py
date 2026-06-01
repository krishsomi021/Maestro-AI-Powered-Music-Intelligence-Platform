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
