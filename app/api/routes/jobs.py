import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import JobStatus, JobType
from app.db.async_session import get_db
from app.models.job import Job
from app.schemas.job import JobCreateResponse, JobListResponse, JobResponse, JobSubmitRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobCreateResponse, status_code=202)
async def submit_job(body: JobSubmitRequest, db: AsyncSession = Depends(get_db)):
    job = Job(
        id=uuid.uuid4(),
        job_type=body.job_type,
        status=JobStatus.PENDING,
        payload=body.payload,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    _enqueue_task(body.job_type, str(job.id))

    logger.info("job %s submitted type=%s", job.id, job.job_type)
    return JobCreateResponse(job_id=job.id, status=job.status, created_at=job.created_at)


def _enqueue_task(job_type: JobType, job_id: str) -> None:
    from worker.celery_app import celery_app

    task_map = {
        JobType.ML_INFERENCE: "worker.tasks.ml_inference.run_ml_inference",
        JobType.ETL_PIPELINE: "worker.tasks.etl_pipeline.run_etl_pipeline",
        JobType.REPORT_GENERATION: "worker.tasks.report_generation.run_report_generation",
    }
    celery_app.send_task(task_map[job_type], args=[job_id])


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        payload=job.payload,
        result=job.result,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("", response_model=JobListResponse)
async def list_jobs(limit: int = 20, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Job).order_by(Job.created_at.desc()).limit(limit)
    )
    jobs = result.scalars().all()
    return JobListResponse(
        jobs=[
            JobResponse(
                job_id=j.id,
                job_type=j.job_type,
                status=j.status,
                payload=j.payload,
                result=j.result,
                error=j.error,
                created_at=j.created_at,
                started_at=j.started_at,
                completed_at=j.completed_at,
            )
            for j in jobs
        ],
        total=len(jobs),
    )
