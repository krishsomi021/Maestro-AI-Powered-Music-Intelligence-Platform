import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import JobStatus, JobType
from app.models.job import Job


async def create_job(
    db: AsyncSession,
    job_type: JobType,
    payload: dict,
    idempotency_key: str | None = None,
) -> Job:
    job = Job(
        id=uuid.uuid4(),
        job_type=job_type,
        status=JobStatus.PENDING,
        payload=payload,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        idempotency_key=idempotency_key,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_by_id(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    result = await db.execute(select(Job).where(Job.id == job_id))
    return result.scalar_one_or_none()


async def get_by_idempotency_key(db: AsyncSession, key: str) -> Job | None:
    result = await db.execute(select(Job).where(Job.idempotency_key == key))
    return result.scalar_one_or_none()


async def get_dead_letter_jobs(db: AsyncSession, limit: int = 20) -> list[Job]:
    result = await db.execute(
        select(Job)
        .where(Job.status == "failed")
        .where(Job.retry_count >= Job.max_retries)
        .order_by(Job.completed_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_jobs(db: AsyncSession, limit: int = 20) -> list[Job]:
    result = await db.execute(
        select(Job).order_by(Job.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def set_cancelled(db: AsyncSession, job: Job) -> Job:
    job.status = "cancelled"
    job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(job)
    return job
