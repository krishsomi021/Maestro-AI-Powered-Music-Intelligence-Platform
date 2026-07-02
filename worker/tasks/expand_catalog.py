import logging
import random
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from worker.celery_app import celery_app
from worker.clients.lastfm import get_lastfm_client
from worker.db.sync_session import get_sync_db
from app.core.normalization import normalize_key
from worker.tasks.base import BaseJobTask

logger = logging.getLogger(__name__)

SEED_COUNT = 20
MIN_MATCH_SCORE = 0.1
SEED_TAG_LIMIT = 5


def _select_seed_tracks(db: Session, limit: int = SEED_COUNT) -> list[dict]:
    """Top `limit` tracks by play count, joined with track_metadata for genres."""
    rows = db.execute(
        text(
            """
            SELECT lh.artist_name, lh.track_name, COUNT(*) AS play_count
            FROM listening_history lh
            GROUP BY lh.artist_name, lh.track_name
            ORDER BY play_count DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).fetchall()
    return [{"artist_name": r[0], "track_name": r[1], "play_count": r[2]} for r in rows]


def _existing_history_keys(db: Session) -> set[str]:
    rows = db.execute(text("SELECT normalized_key FROM listening_history")).fetchall()
    return {r[0] for r in rows}


def _generate_candidates(db: Session, client) -> list[dict]:
    """
    For each seed track: fetch similar tracks + the seed's own tags (once per seed,
    not per candidate — Last.fm has no per-candidate tag budget here). Filter below
    MIN_MATCH_SCORE, dedup by normalized_key, and drop anything already listened to.
    """
    seeds = _select_seed_tracks(db)
    if not seeds:
        logger.info("expand_catalog: no seed tracks available — nothing to do")
        return []

    history_keys = _existing_history_keys(db)
    candidates: dict[str, dict] = {}  # normalized_key -> candidate row

    for seed in seeds:
        try:
            seed_tags = client.get_top_tags(seed["artist_name"], seed["track_name"], limit=SEED_TAG_LIMIT)
        except Exception as exc:
            logger.warning(
                "expand_catalog: getTopTags failed for seed %r/%r: %s",
                seed["artist_name"], seed["track_name"], exc,
            )
            seed_tags = []

        try:
            similar = client.get_similar_tracks(seed["artist_name"], seed["track_name"])
        except Exception as exc:
            logger.warning(
                "expand_catalog: getSimilar failed for seed %r/%r: %s",
                seed["artist_name"], seed["track_name"], exc,
            )
            continue

        for item in similar:
            if item["match"] < MIN_MATCH_SCORE:
                continue

            key = normalize_key(item["artist_name"], item["track_name"])
            if key in history_keys:
                continue
            if key in candidates and candidates[key]["lastfm_match_score"] >= item["match"]:
                continue  # keep the higher-scoring occurrence if seen from multiple seeds

            candidates[key] = {
                "artist_name": item["artist_name"],
                "track_name": item["track_name"],
                "normalized_key": key,
                "lastfm_url": item.get("url"),
                "lastfm_match_score": item["match"],
                "seed_track_name": seed["track_name"],
                "global_play_count": None,
                "tags": seed_tags,
            }

    return list(candidates.values())


def _embed_candidates(candidates: list[dict]) -> list[dict]:
    from worker.ml.embeddings import embed_tracks

    tracks_for_embedding = [
        {"artist_name": c["artist_name"], "track_name": c["track_name"], "genres": c["tags"]}
        for c in candidates
    ]
    try:
        vectors = embed_tracks(tracks_for_embedding)
    except Exception as exc:
        logger.warning("expand_catalog: embedding generation failed: %s", exc)
        vectors = [None] * len(candidates)

    for candidate, vector in zip(candidates, vectors):
        candidate["embedding"] = vector
    return candidates


def _upsert_candidates(db: Session, candidates: list[dict]) -> int:
    from app.models.external_catalog import ExternalCatalog

    if not candidates:
        return 0

    stmt = pg_insert(ExternalCatalog).values(candidates)
    stmt = stmt.on_conflict_do_update(
        index_elements=["normalized_key"],
        set_={
            "lastfm_url": stmt.excluded.lastfm_url,
            "lastfm_match_score": stmt.excluded.lastfm_match_score,
            "seed_track_name": stmt.excluded.seed_track_name,
            "tags": stmt.excluded.tags,
            "embedding": stmt.excluded.embedding,
            "last_refreshed_at": text("clock_timestamp()"),
        },
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount


@celery_app.task(name="worker.tasks.expand_catalog.expand_catalog", bind=True, base=BaseJobTask)
def expand_catalog(self, job_id: str) -> None:
    from app.models.job import Job

    db = get_sync_db()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        client = get_lastfm_client()
        if client is None:
            error_msg = "LASTFM_API_KEY is not configured"
            logger.error("job %s expand_catalog failed: %s", job_id, error_msg)
            job.status = "failed"
            job.error = error_msg
            job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
            return

        candidates = _generate_candidates(db, client)

        db.refresh(job)
        if job.status == "cancelled":
            logger.info("job %s cancelled during execution", job_id)
            return

        candidates = _embed_candidates(candidates)
        upserted = _upsert_candidates(db, candidates)

        job.result = {
            "status": "success",
            "candidates_generated": len(candidates),
            "candidates_upserted": upserted,
            "pipeline": "expand_catalog",
        }
        job.status = "complete"
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.info("job %s complete expand_catalog upserted=%d", job_id, upserted)
    except Exception as exc:
        retry_num = self.request.retries
        countdown = (2 ** retry_num) + random.uniform(0, 0.3 * (2 ** retry_num))
        raise self.retry(exc=exc, countdown=countdown)
    finally:
        db.close()
