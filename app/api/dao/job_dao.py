import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import JobStatus, JobType
from app.models.job import Job


async def create_job(db: AsyncSession, job_type: JobType, payload: dict) -> Job:
    job = Job(
        id=uuid.uuid4(),
        job_type=job_type,
        status=JobStatus.PENDING,
        payload=payload,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_job_by_id(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    result = await db.execute(select(Job).where(Job.id == job_id))
    return result.scalar_one_or_none()


async def list_jobs(db: AsyncSession, limit: int = 20) -> list[Job]:
    result = await db.execute(
        select(Job).order_by(Job.created_at.desc()).limit(limit)
    )
    return result.scalars().all()