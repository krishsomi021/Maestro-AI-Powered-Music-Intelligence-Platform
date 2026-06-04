import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dao import job_dao
from app.api.services import job_service
from app.core.enums import JobStatus
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
async def submit_job_route(
    body: JobSubmitRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    job, is_new = await job_service.submit_job(db, body.job_type, body.payload, body.idempotency_key)
    response.status_code = status.HTTP_202_ACCEPTED if is_new else status.HTTP_200_OK
    return JobCreateResponse(job_id=job.id, status=job.status, created_at=job.created_at)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    job = await job_dao.get_by_id(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _to_response(job)


@router.get("", response_model=JobListResponse)
async def list_jobs(limit: int = 20, db: AsyncSession = Depends(get_db)):
    jobs = await job_dao.list_jobs(db, limit)
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
