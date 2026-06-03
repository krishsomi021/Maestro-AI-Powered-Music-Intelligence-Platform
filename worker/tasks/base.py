import logging
import uuid
from datetime import datetime, timezone

from celery import Task, shared_task

logger = logging.getLogger(__name__)


class BaseJobTask(Task):
    abstract = True
    max_retries = 3

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        job_id = args[0] if args else kwargs.get("job_id")
        if not job_id:
            return
        from worker.db.sync_session import get_sync_db
        db = get_sync_db()
        try:
            from app.models.job import Job
            job = db.get(Job, uuid.UUID(job_id))
            if job:
                job.status = "retrying"
                job.retry_count = self.request.retries
                job.error = str(exc)
                db.commit()
                logger.info("job %s retrying retry_count=%d", job_id, job.retry_count)
        except Exception:
            logger.exception("on_retry: failed to update job %s", job_id)
        finally:
            db.close()

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        job_id = args[0] if args else kwargs.get("job_id")
        if not job_id:
            return
        from worker.db.sync_session import get_sync_db
        db = get_sync_db()
        try:
            from app.models.job import Job
            job = db.get(Job, uuid.UUID(job_id))
            if job and job.status != "cancelled":
                job.status = "failed"
                job.error = str(exc)
                if not job.completed_at:
                    job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()
            from worker.celery_app import celery_app
            celery_app.send_task(
                "worker.tasks.base.handle_dead_letter",
                args=[job_id, str(exc)],
                queue="dead_letter",
            )
            logger.error("job %s permanently failed, routed to dead_letter: %s", job_id, exc)
        except Exception:
            logger.exception("on_failure: failed to handle job %s", job_id)
        finally:
            db.close()


@shared_task(name="worker.tasks.base.handle_dead_letter")
def handle_dead_letter(job_id: str, error: str) -> None:
    logger.error("DEAD LETTER job=%s error=%s", job_id, error)
