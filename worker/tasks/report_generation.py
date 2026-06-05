import logging
import random
import time
import uuid
from datetime import datetime, timezone

from worker.celery_app import celery_app
from worker.db.sync_session import get_sync_db
from worker.tasks.base import BaseJobTask

logger = logging.getLogger(__name__)


@celery_app.task(name="worker.tasks.report_generation.run_report_generation", bind=True, base=BaseJobTask)
def run_report_generation(self, job_id: str) -> None:
    from app.models.job import Job

    db = get_sync_db()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        sleep_secs = random.uniform(3, 6)
        time.sleep(sleep_secs)

        db.refresh(job)
        if job.status == "cancelled":
            logger.info("job %s cancelled during execution", job_id)
            return

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
        retry_num = self.request.retries
        countdown = (2 ** retry_num) + random.uniform(0, 0.3 * (2 ** retry_num))
        raise self.retry(exc=exc, countdown=countdown)
    finally:
        db.close()
