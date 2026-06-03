import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dao import job_dao
from app.models.job import Job


async def find_by_idempotency_key(db: AsyncSession, key: str) -> Job | None:
    return await job_dao.get_by_idempotency_key(db, key)


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
