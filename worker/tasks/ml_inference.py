import logging
import random
import uuid
from datetime import datetime, timezone

import joblib
import numpy as np

from worker.celery_app import celery_app
from worker.db.sync_session import get_sync_db
from worker.tasks.base import BaseJobTask

logger = logging.getLogger(__name__)

_model = None


def _load_model():
    global _model
    if _model is None:
        _model = joblib.load("models/fraud_model.joblib")
    return _model


@celery_app.task(name="worker.tasks.ml_inference.run_ml_inference", bind=True, base=BaseJobTask)
def run_ml_inference(self, job_id: str) -> None:
    from app.models.job import Job

    db = get_sync_db()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        model = _load_model()
        features = np.array(job.payload["features"]).reshape(1, -1)
        prediction = int(model.predict(features)[0])
        prediction_label = "fraud" if prediction == 1 else "not_fraud"

        db.refresh(job)
        if job.status == "cancelled":
            logger.info("job %s cancelled during execution", job_id)
            return

        job.result = {
            "prediction": prediction,
            "prediction_label": prediction_label,
            "model": "fraud_model",
        }
        job.status = "complete"
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.info("job %s complete prediction=%s", job_id, prediction_label)
    except Exception as exc:
        retry_num = self.request.retries
        countdown = (2 ** retry_num) + random.uniform(0, 0.3 * (2 ** retry_num))
        raise self.retry(exc=exc, countdown=countdown)
    finally:
        db.close()
