import logging
import random
import time
import uuid
from datetime import datetime, timezone

from worker.celery_app import celery_app
from worker.db.sync_session import get_sync_db

logger = logging.getLogger(__name__)


@celery_app.task(name="worker.tasks.report_generation.run_report_generation")
def run_report_generation(job_id: str) -> None:
    from app.models.job import Job

    db = get_sync_db()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        sleep_secs = random.uniform(3, 6)
        time.sleep(sleep_secs)

        report_id = str(uuid.uuid4())
        job.result = {
            "report_id": report_id,
            "pages": random.randint(5, 30),
            "format": "pdf",
            "download_url": f"mocked://reports/{report_id}.pdf",
        }
        job.status = "complete"
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.info("job %s complete report=%s", job_id, report_id)
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.exception("job %s failed", job_id)
        raise
    finally:
        db.close()
