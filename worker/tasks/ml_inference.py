import logging
import random
import uuid
from datetime import datetime, timezone

import joblib
import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from worker.celery_app import celery_app
from worker.db.sync_session import get_sync_db
from worker.tasks.base import BaseJobTask

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
            meta = uri_to_meta.get(seed_uri, {})
            if meta:
                fallback = f"{meta.get('artist_name', '').strip().lower()}|||{meta.get('track_name', '').strip().lower()}"
                neighbours = similarity.get(fallback)
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


def _run_pgvector_recommender(payload: dict, db: Session) -> dict:
    """Content-based recommender: cosine similarity over 384-dim embeddings stored in track_metadata."""
    seed_uris: list[str] = payload.get("seed_tracks", [])
    top_n: int = int(payload.get("top_n", 10))

    # Parse spotify_track_id from "spotify:track:{id}" URIs
    seed_ids: list[str] = []
    for uri in seed_uris:
        parts = uri.split(":")
        if len(parts) == 3 and parts[0] == "spotify" and parts[1] == "track":
            seed_ids.append(parts[2])
        else:
            logger.warning("pgvector recommender: unparseable seed URI %r — skipping", uri)

    if not seed_ids:
        return {"recommendations": [], "seed_count": 0, "model": "pgvector_recommender", "top_n": top_n}

    rows = db.execute(
        text(
            "SELECT spotify_track_id, embedding FROM track_metadata "
            "WHERE spotify_track_id = ANY(:ids) AND embedding IS NOT NULL"
        ),
        {"ids": seed_ids},
    ).fetchall()

    if not rows:
        logger.warning(
            "pgvector recommender: none of %d seed ID(s) found in track_metadata", len(seed_ids)
        )
        return {"recommendations": [], "seed_count": 0, "model": "pgvector_recommender", "top_n": top_n}

    found_seed_ids = {r[0] for r in rows}
    for sid in seed_ids:
        if sid not in found_seed_ids:
            logger.warning("pgvector recommender: seed ID %r not in track_metadata — skipping", sid)

    centroid = np.mean([np.array(r[1]) for r in rows], axis=0).tolist()

    candidates = db.execute(
        text(
            """
            SELECT artist_name, track_name, spotify_track_id,
                   1 - (embedding <=> CAST(:centroid AS vector)) AS score
            FROM track_metadata
            WHERE embedding IS NOT NULL
              AND (spotify_track_id IS NULL OR spotify_track_id != ALL(:seed_ids))
            ORDER BY embedding <=> CAST(:centroid AS vector)
            LIMIT :top_n
            """
        ),
        {"centroid": str(centroid), "seed_ids": seed_ids, "top_n": top_n},
    ).fetchall()

    recommendations = [
        {
            "uri": f"spotify:track:{row[2]}" if row[2] else "",
            "track_name": row[1],
            "artist_name": row[0],
            "score": round(float(row[3]), 6),
        }
        for row in candidates
    ]

    return {
        "recommendations": recommendations,
        "seed_count": len(rows),
        "model": "pgvector_recommender",
        "top_n": top_n,
    }


@celery_app.task(name="worker.tasks.ml_inference.run_ml_inference", bind=True, base=BaseJobTask)
def run_ml_inference(self, job_id: str) -> None:
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
        elif payload.get("recommender") == "pgvector":
            result = _run_pgvector_recommender(payload, db)
        else:
            result = _run_recommender(payload)

        db.refresh(job)
        if job.status == "cancelled":
            logger.info("job %s cancelled during execution", job_id)
            return

        job.result = result
        job.status = "complete"
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.info("job %s complete mode=%s", job_id, result.get("model"))
    except Exception as exc:
        retry_num = self.request.retries
        countdown = (2 ** retry_num) + random.uniform(0, 0.3 * (2 ** retry_num))
        raise self.retry(exc=exc, countdown=countdown)
    finally:
        db.close()
