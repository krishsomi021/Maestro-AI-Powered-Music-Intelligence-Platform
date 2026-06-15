from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import select

from app.models.job import Job

SCHEDULED_ETL_IDEMPOTENCY_KEY = "scheduled_etl_daily"


def _run_with_session(sync_db_session, monkeypatch):
    """trigger_etl_sync owns its session lifecycle (closes it in `finally`); neutralize
    that so it doesn't tear down the fixture's session mid-test."""
    monkeypatch.setattr(sync_db_session, "close", lambda: None)
    with patch("worker.tasks.scheduled.get_sync_db", return_value=sync_db_session), \
         patch("worker.tasks.etl_pipeline.run_etl_pipeline") as mock_run_etl:
        from worker.tasks.scheduled import trigger_etl_sync
        trigger_etl_sync()
    return mock_run_etl


def _scheduled_jobs(sync_db_session):
    return list(sync_db_session.execute(
        select(Job).where(Job.idempotency_key == SCHEDULED_ETL_IDEMPOTENCY_KEY)
    ).scalars().all())


def test_trigger_etl_sync_creates_job(sync_db_session, clean_scheduled_jobs, monkeypatch):
    mock_run_etl = _run_with_session(sync_db_session, monkeypatch)

    jobs = _scheduled_jobs(sync_db_session)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.job_type == "etl_pipeline"
    assert job.status == "pending"
    assert job.payload == {"source": "data/spotify_export", "destination": "listening_history"}
    assert job.idempotency_key == SCHEDULED_ETL_IDEMPOTENCY_KEY

    mock_run_etl.apply_async.assert_called_once_with(args=[str(job.id)], task_id=str(job.id))


def test_trigger_etl_sync_skips_when_active_job_exists(sync_db_session, clean_scheduled_jobs, monkeypatch):
    sync_db_session.add(Job(
        job_type="etl_pipeline",
        status="running",
        payload={"source": "data/spotify_export"},
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        idempotency_key=SCHEDULED_ETL_IDEMPOTENCY_KEY,
    ))
    sync_db_session.commit()

    mock_run_etl = _run_with_session(sync_db_session, monkeypatch)

    assert len(_scheduled_jobs(sync_db_session)) == 1
    mock_run_etl.apply_async.assert_not_called()


def test_trigger_etl_sync_creates_new_job_after_terminal_job(sync_db_session, clean_scheduled_jobs, monkeypatch):
    sync_db_session.add(Job(
        job_type="etl_pipeline",
        status="complete",
        payload={"source": "data/spotify_export"},
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        idempotency_key=SCHEDULED_ETL_IDEMPOTENCY_KEY,
    ))
    sync_db_session.commit()

    mock_run_etl = _run_with_session(sync_db_session, monkeypatch)

    jobs = _scheduled_jobs(sync_db_session)
    assert len(jobs) == 2
    new_job = next(j for j in jobs if j.status == "pending")
    assert new_job.idempotency_key == SCHEDULED_ETL_IDEMPOTENCY_KEY

    mock_run_etl.apply_async.assert_called_once_with(args=[str(new_job.id)], task_id=str(new_job.id))
