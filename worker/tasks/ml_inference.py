import logging
import uuid
from datetime import datetime, timezone

import joblib
import numpy as np

from worker.celery_app import celery_app
from worker.db.sync_session import get_sync_db

logger = logging.getLogger(__name__)

_model = None


def _load_model() -> dict:
    global _model
    if _model is None:
        _model = joblib.load("models/spotify_recommender.joblib")
    return _model


@celery_app.task(name="worker.tasks.ml_inference.run_ml_inference")
def run_ml_inference(job_id: str) -> None:
    from app.models.job import Job

    db = get_sync_db()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        model = _load_model()
        matrix = model["matrix"]
        track_ids = model["track_ids"]
        track_id_to_idx = model["track_id_to_idx"]
        track_lookup = model["track_lookup"]

        seed_tracks: list[str] = job.payload.get("seed_tracks", [])
        top_n: int = int(job.payload.get("top_n", 10))

        scores = np.zeros(len(track_ids), dtype=np.float32)
        found_seeds = []
        for seed in seed_tracks:
            idx = track_id_to_idx.get(seed)
            if idx is not None:
                scores += matrix[idx]
                found_seeds.append(seed)

        # Zero out seeds so they don't appear in results
        for seed in found_seeds:
            idx = track_id_to_idx.get(seed)
            if idx is not None:
                scores[idx] = 0.0

        top_indices = np.argsort(scores)[::-1][:top_n]
        recommendations = []
        for i in top_indices:
            if scores[i] <= 0:
                break
            tid = track_ids[i]
            info = track_lookup.get(tid, {})
            recommendations.append({
                "track_id": tid,
                "track_name": info.get("track_name"),
                "artist": info.get("artist"),
                "score": float(round(float(scores[i]), 4)),
            })

        job.result = {
            "recommendations": recommendations,
            "seed_count": len(found_seeds),
            "model": "spotify_recommender",
        }
        job.status = "complete"
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.info(
            "job %s complete recs=%d seed_count=%d",
            job_id,
            len(recommendations),
            len(found_seeds),
        )
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.exception("job %s failed", job_id)
        raise
    finally:
        db.close()
