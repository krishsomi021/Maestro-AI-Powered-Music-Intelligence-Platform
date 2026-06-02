import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.async_session import get_db
from app.schemas.job import JobCreateResponse, JobListResponse, JobResponse, JobSubmitRequest
from app.api.dao.job_dao import get_job_by_id, list_jobs
from app.api.services.job_service import submit_job


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobCreateResponse, status_code=202)
async def submit_job_route(body: JobSubmitRequest, db: AsyncSession = Depends(get_db)):
    job = await submit_job(db, body.job_type, body.payload)
    return JobCreateResponse(job_id=job.id, status=job.status, created_at=job.created_at)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job_route(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    job = await get_job_by_id(db, job_id)
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
async def list_jobs_route(limit: int = 20, db: AsyncSession = Depends(get_db)):
    jobs = await list_jobs(db, limit)
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