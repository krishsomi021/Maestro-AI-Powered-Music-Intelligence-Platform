import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.services import job_service
from app.core.enums import JobStatus, JobType
from app.db.async_session import get_db
from app.models.job import Job
from app.schemas.job import (
    CancelJobResponse,
    JobCreateResponse,
    JobListResponse,
    JobResponse,
    JobSubmitRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _to_response(job: Job) -> JobResponse:
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
        idempotency_key=job.idempotency_key,
        retry_count=job.retry_count,
    )


# Must be registered before /{job_id} to avoid parameterised route capturing it
@router.get("/dead-letter", response_model=JobListResponse)
async def list_dead_letter_jobs(limit: int = 20, db: AsyncSession = Depends(get_db)):
    jobs = await job_service.get_dead_letter_jobs(db, limit)
    return JobListResponse(jobs=[_to_response(j) for j in jobs], total=len(jobs))


@router.post("", response_model=JobCreateResponse)
async def submit_job(
    body: JobSubmitRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    if body.idempotency_key:
        existing = await job_service.find_by_idempotency_key(db, body.idempotency_key)
        if existing:
            response.status_code = status.HTTP_200_OK
            return JobCreateResponse(
                job_id=existing.id,
                status=existing.status,
                created_at=existing.created_at,
            )

    job = Job(
        id=uuid.uuid4(),
        job_type=body.job_type,
        status=JobStatus.PENDING,
        payload=body.payload,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        idempotency_key=body.idempotency_key,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    _enqueue_task(body.job_type, str(job.id))

    response.status_code = status.HTTP_202_ACCEPTED
    logger.info("job %s submitted type=%s", job.id, job.job_type)
    return JobCreateResponse(job_id=job.id, status=job.status, created_at=job.created_at)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _to_response(job)


@router.get("", response_model=JobListResponse)
async def list_jobs(limit: int = 20, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Job).order_by(Job.created_at.desc()).limit(limit)
    )
    jobs = result.scalars().all()
    return JobListResponse(jobs=[_to_response(j) for j in jobs], total=len(jobs))


@router.delete("/{job_id}", response_model=CancelJobResponse)
async def cancel_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await job_service.cancel_job(db, job_id)
    if not result["found"]:
        raise HTTPException(status_code=404, detail="Job not found")
    if not result["cancelled"]:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel job with status '{result['status']}'",
        )
    return CancelJobResponse(job_id=job_id, status=JobStatus.CANCELLED)


def _enqueue_task(job_type: JobType, job_id: str) -> None:
    from worker.celery_app import celery_app

    task_map = {
        JobType.ML_INFERENCE: "worker.tasks.ml_inference.run_ml_inference",
        JobType.ETL_PIPELINE: "worker.tasks.etl_pipeline.run_etl_pipeline",
        JobType.REPORT_GENERATION: "worker.tasks.report_generation.run_report_generation",
    }
    celery_app.send_task(task_map[job_type], args=[job_id], task_id=job_id)
