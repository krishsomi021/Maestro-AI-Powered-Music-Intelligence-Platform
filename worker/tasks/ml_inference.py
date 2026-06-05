import logging
import uuid
from datetime import datetime, timezone

import joblib
import numpy as np

from worker.celery_app import celery_app
from worker.db.sync_session import get_sync_db

logger = logging.getLogger(__name__)

_fraud_model = None
_recommender_model = None


def _load_fraud_model():
    global _fraud_model
    if _fraud_model is None:
        _fraud_model = joblib.load("models/fraud_model.joblib")
    return _fraud_model


def _load_recommender_model() -> dict:
    global _recommender_model
    if _recommender_model is None:
        _recommender_model = joblib.load("models/spotify_recommender.joblib")
    return _recommender_model


def _run_fraud_detection(payload: dict) -> dict:
    model = _load_fraud_model()
    features = np.array(payload["features"]).reshape(1, -1)
    prediction = int(model.predict(features)[0])
    return {
        "prediction": prediction,
        "prediction_label": "fraud" if prediction == 1 else "not_fraud",
        "model": "fraud_model",
    }


def _run_recommender(payload: dict) -> dict:
    model = _load_recommender_model()
    similarity: dict = model["similarity"]
    uri_to_meta: dict = model["uri_to_meta"]

    seed_tracks: list[str] = payload.get("seed_tracks", [])
    top_n: int = int(payload.get("top_n", 10))

    aggregated: dict[str, float] = {}
    found_seeds: list[str] = []

    for seed_uri in seed_tracks:
        neighbours = similarity.get(seed_uri)
        if neighbours is None:
            logger.warning("seed URI not found in model, skipping: %s", seed_uri)
            continue
        found_seeds.append(seed_uri)
        for target, score in neighbours.items():
            aggregated[target] = aggregated.get(target, 0.0) + score

    if not found_seeds:
        logger.warning("no seed URIs were found in the model — returning empty recommendations")

    # Exclude seed tracks from results
    seed_set = set(seed_tracks)
    candidates = [
        (uri, score)
        for uri, score in aggregated.items()
        if uri not in seed_set
    ]
    candidates.sort(key=lambda x: x[1], reverse=True)

    recommendations = []
    for uri, score in candidates[:top_n]:
        meta = uri_to_meta.get(uri, {})
        recommendations.append({
            "uri": uri,
            "track_name": meta.get("track_name", ""),
            "artist_name": meta.get("artist_name", ""),
            "score": round(score, 6),
        })

    return {
        "recommendations": recommendations,
        "seed_count": len(found_seeds),
        "model": "spotify_recommender",
        "top_n": top_n,
    }


@celery_app.task(name="worker.tasks.ml_inference.run_ml_inference")
def run_ml_inference(job_id: str) -> None:
    from app.models.job import Job

    db = get_sync_db()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        payload = job.payload
        if "features" in payload:
            result = _run_fraud_detection(payload)
        else:
            result = _run_recommender(payload)

        job.result = result
        job.status = "complete"
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.info("job %s complete mode=%s", job_id, result.get("model"))
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.exception("job %s failed", job_id)
        raise
    finally:
        db.close()
