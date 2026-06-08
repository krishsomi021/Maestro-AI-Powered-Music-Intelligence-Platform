import logging

from celery import Celery
from celery.schedules import crontab

from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "job_processor",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "worker.tasks.base",
        "worker.tasks.ml_inference",
        "worker.tasks.etl_pipeline",
        "worker.tasks.report_generation",
        "worker.tasks.scheduled",
    ],
)

celery_app.conf.update(
    task_acks_late=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_default_queue="celery",
    beat_schedule={
        "trigger-etl-sync-daily": {
            "task": "worker.tasks.scheduled.trigger_etl_sync",
            "schedule": crontab(hour=settings.beat_etl_hour, minute=settings.beat_etl_minute),
        },
    },
)
