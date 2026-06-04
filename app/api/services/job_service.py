import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dao import job_dao
from app.core.enums import JobType
from app.models.job import Job

logger = logging.getLogger(__name__)


async def submit_job(
    db: AsyncSession,
    job_type: JobType,
    payload: dict,
    idempotency_key: str | None = None,
) -> tuple[Job, bool]:
    """Return (job, is_new). is_new=False signals an idempotency hit (existing job)."""
    if idempotency_key:
        existing = await job_dao.get_by_idempotency_key(db, idempotency_key)
        if existing:
            return existing, False

    job = await job_dao.create_job(db, job_type, payload, idempotency_key)
    _enqueue_task(job_type, str(job.id))
    logger.info("job %s submitted type=%s", job.id, job_type)
    return job, True


async def get_dead_letter_jobs(db: AsyncSession, limit: int = 20) -> list[Job]:
    return await job_dao.get_dead_letter_jobs(db, limit)


async def cancel_job(db: AsyncSession, job_id: uuid.UUID) -> dict:
    job = await job_dao.get_by_id(db, job_id)
    if job is None:
        return {"found": False}

    terminal_statuses = {"complete", "failed", "cancelled"}
    if job.status in terminal_statuses:
        return {"found": True, "cancelled": False, "status": job.status}

    from worker.celery_app import celery_app
    celery_app.control.revoke(str(job_id), terminate=True)

    await job_dao.set_cancelled(db, job)
    return {"found": True, "cancelled": True, "status": "cancelled"}


def _enqueue_task(job_type: JobType, job_id: str) -> None:
    from worker.celery_app import celery_app

    task_map = {
        JobType.ML_INFERENCE: "worker.tasks.ml_inference.run_ml_inference",
        JobType.ETL_PIPELINE: "worker.tasks.etl_pipeline.run_etl_pipeline",
        JobType.REPORT_GENERATION: "worker.tasks.report_generation.run_report_generation",
    }
    # task_id=job_id enables celery.control.revoke(job_id) without an extra DB column
    celery_app.send_task(task_map[job_type], args=[job_id], task_id=job_id)
