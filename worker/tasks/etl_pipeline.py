import logging
import random
import time
import uuid
from datetime import datetime, timezone

from worker.celery_app import celery_app
from worker.db.sync_session import get_sync_db

logger = logging.getLogger(__name__)


@celery_app.task(name="worker.tasks.etl_pipeline.run_etl_pipeline")
def run_etl_pipeline(job_id: str) -> None:
    from app.models.job import Job

    db = get_sync_db()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        sleep_secs = random.uniform(5, 8)
        time.sleep(sleep_secs)

        job.result = {
            "rows_processed": random.randint(10000, 20000),
            "duration_seconds": round(sleep_secs, 1),
            "status": "success",
        }
        job.status = "complete"
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.info("job %s complete etl", job_id)
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.exception("job %s failed", job_id)
        raise
    finally:
        db.close()
