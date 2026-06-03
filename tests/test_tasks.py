import uuid
from unittest.mock import MagicMock, patch

import pytest


def _make_job(job_id, payload, status="pending"):
    job = MagicMock()
    job.id = uuid.UUID(job_id)
    job.payload = payload
    job.status = status
    job.started_at = None
    job.completed_at = None
    job.result = None
    job.error = None
    job.retry_count = 0
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


@patch("worker.tasks.etl_pipeline.get_sync_db")
@patch("worker.tasks.etl_pipeline.time.sleep")
def test_etl_cancelled_job_skips_completion(mock_sleep, mock_get_db):
    """Worker checks status after sleep; if cancelled it exits without setting complete."""
    job_id = str(uuid.uuid4())
    mock_job = _make_job(job_id, {"source": "a", "destination": "b"}, status="running")

    call_count = 0

    def refresh_side_effect(job):
        nonlocal call_count
        call_count += 1
        job.status = "cancelled"

    mock_db = MagicMock()
    mock_db.get.return_value = mock_job
    mock_db.refresh.side_effect = refresh_side_effect
    mock_get_db.return_value = mock_db

    from worker.tasks.etl_pipeline import run_etl_pipeline

    run_etl_pipeline(job_id)

    assert mock_job.status == "cancelled"
    mock_db.close.assert_called_once()


@patch("worker.db.sync_session.get_sync_db")
def test_base_on_retry_sets_retrying_status(mock_get_db):
    job_id = str(uuid.uuid4())
    mock_job = _make_job(job_id, {})
    mock_db = MagicMock()
    mock_db.get.return_value = mock_job
    mock_get_db.return_value = mock_db

    from worker.tasks.base import BaseJobTask

    task = BaseJobTask()
    task.request = MagicMock()
    task.request.retries = 1

    with patch("worker.db.sync_session.get_sync_db", return_value=mock_db):
        task.on_retry(exc=RuntimeError("boom"), task_id=job_id, args=[job_id], kwargs={}, einfo=None)

    assert mock_job.status == "retrying"
    assert mock_job.retry_count == 1
    mock_db.commit.assert_called_once()
    mock_db.close.assert_called_once()


def test_base_on_failure_sets_failed_status():
    job_id = str(uuid.uuid4())
    mock_job = _make_job(job_id, {})
    mock_db = MagicMock()
    mock_db.get.return_value = mock_job
    mock_celery = MagicMock()

    from worker.tasks.base import BaseJobTask

    task = BaseJobTask()
    with patch("worker.db.sync_session.get_sync_db", return_value=mock_db), \
         patch("worker.celery_app.celery_app", mock_celery):
        task.on_failure(exc=RuntimeError("fatal"), task_id=job_id, args=[job_id], kwargs={}, einfo=None)

    assert mock_job.status == "failed"
    assert mock_job.error == "fatal"
    mock_db.close.assert_called_once()


def test_base_on_failure_skips_cancelled_job():
    """on_failure must not overwrite a cancelled job's status."""
    job_id = str(uuid.uuid4())
    mock_job = _make_job(job_id, {}, status="cancelled")
    mock_db = MagicMock()
    mock_db.get.return_value = mock_job
    mock_celery = MagicMock()

    from worker.tasks.base import BaseJobTask

    task = BaseJobTask()
    with patch("worker.db.sync_session.get_sync_db", return_value=mock_db), \
         patch("worker.celery_app.celery_app", mock_celery):
        task.on_failure(exc=RuntimeError("revoked"), task_id=job_id, args=[job_id], kwargs={}, einfo=None)

    assert mock_job.status == "cancelled"
