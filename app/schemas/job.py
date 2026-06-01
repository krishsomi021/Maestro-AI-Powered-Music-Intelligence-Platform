import uuid
from datetime import datetime

from pydantic import BaseModel

from app.core.enums import JobStatus, JobType


class JobSubmitRequest(BaseModel):
    job_type: JobType
    payload: dict


class JobResponse(BaseModel):
    job_id: uuid.UUID
    job_type: JobType
    status: JobStatus
    payload: dict
    result: dict | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class JobCreateResponse(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    created_at: datetime


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
