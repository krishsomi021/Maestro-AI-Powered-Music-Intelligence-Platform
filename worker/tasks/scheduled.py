import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.enums import JobStatus, JobType
from worker.celery_app import celery_app
from worker.db.sync_session import get_sync_db

logger = logging.getLogger(__name__)

SCHEDULED_ETL_IDEMPOTENCY_KEY = "scheduled_etl_daily"


@celery_app.task(name="worker.tasks.scheduled.trigger_etl_sync")
def trigger_etl_sync() -> None:
    """Meta-orchestrator: creates and enqueues the nightly ETL job. Not a BaseJobTask —
    it doesn't process a job, it schedules one. On failure Beat simply retries it at the
    next scheduled interval, so no retry/dead-letter handling is needed here."""
    from app.models.job import Job
    from worker.tasks.etl_pipeline import run_etl_pipeline

    db = get_sync_db()
    try:
        terminal_statuses = (JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED)
        active = db.execute(
            select(Job)
            .where(Job.idempotency_key == SCHEDULED_ETL_IDEMPOTENCY_KEY)
            .where(Job.status.not_in(terminal_statuses))
            .limit(1)
        ).scalars().first()

        if active is not None:
            logger.info(
                "scheduled ETL skipped — job %s already active (status=%s)",
                active.id, active.status,
            )
            return

        job = Job(
            id=uuid.uuid4(),
            job_type=JobType.ETL_PIPELINE,
            status=JobStatus.PENDING,
            payload={"source": "data/spotify_export", "destination": "listening_history"},
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            idempotency_key=SCHEDULED_ETL_IDEMPOTENCY_KEY,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        run_etl_pipeline.apply_async(args=[str(job.id)], task_id=str(job.id))
        logger.info("scheduled ETL job %s created and enqueued", job.id)
    finally:
        db.close()
