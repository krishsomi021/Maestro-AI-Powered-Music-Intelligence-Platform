import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dao.job_dao import create_job, get_job_by_id, list_jobs
from app.core.enums import JobType
from app.models.job import Job

logger = logging.getLogger(__name__)


import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import JobType
from app.models.job import Job
from app.api.dao.job_dao import create_job


logger = logging.getLogger(__name__)


async def submit_job(db: AsyncSession, job_type: JobType, payload: dict) -> Job:
    job = await create_job(db, job_type, payload)
    _enqueue_task(job_type, str(job.id))
    logger.info("job %s submitted type=%s", job.id, job_type)
    return job


def _enqueue_task(job_type: JobType, job_id: str) -> None:
    from worker.celery_app import celery_app

    task_map = {
        JobType.ML_INFERENCE: "worker.tasks.ml_inference.run_ml_inference",
        JobType.ETL_PIPELINE: "worker.tasks.etl_pipeline.run_etl_pipeline",
        JobType.REPORT_GENERATION: "worker.tasks.report_generation.run_report_generation",
    }
    celery_app.send_task(task_map[job_type], args=[job_id])