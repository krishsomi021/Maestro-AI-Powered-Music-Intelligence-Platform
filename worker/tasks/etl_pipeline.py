import json
import logging
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from worker.celery_app import celery_app
from worker.clients.spotify import get_spotify_client
from worker.db.sync_session import get_sync_db
from worker.tasks.base import BaseJobTask

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_DIR = "data/spotify_export"
SOURCE_FILE_GLOB = "StreamingHistory_music_*.json"
END_TIME_FORMAT = "%Y-%m-%d %H:%M"
MIN_MS_PLAYED = 30000
LOAD_BATCH_SIZE = 500
ENRICH_BATCH_LIMIT = 200       # max tracks resolved per ETL run
SEARCH_DELAY_SECONDS = 0.2     # polite delay between search_track() calls


def _find_source_files(source_dir: Path) -> list[Path]:
    if not source_dir.is_dir():
        return []
    return sorted(source_dir.glob(SOURCE_FILE_GLOB))


def _extract(files: list[Path]) -> list[dict]:
    entries: list[dict] = []
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            entries.extend(json.load(f))
    return entries


def _transform(entries: list[dict]) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    skipped_short_plays = 0
    skipped_invalid = 0

    for entry in entries:
        ms_played = entry.get("msPlayed")
        artist_name = entry.get("artistName")
        track_name = entry.get("trackName")
        end_time_raw = entry.get("endTime")

        if not isinstance(ms_played, (int, float)) or ms_played < MIN_MS_PLAYED:
            skipped_short_plays += 1
            continue

        if not artist_name or not track_name:
            skipped_invalid += 1
            continue

        if "podcast" in artist_name.lower():
            skipped_invalid += 1
            continue

        try:
            end_time = datetime.strptime(end_time_raw, END_TIME_FORMAT)
        except (TypeError, ValueError):
            skipped_invalid += 1
            continue

        rows.append({
            "artist_name": artist_name,
            "track_name": track_name,
            "end_time": end_time,
            "ms_played": int(ms_played),
        })

    stats = {"skipped_short_plays": skipped_short_plays, "skipped_invalid": skipped_invalid}
    return rows, stats


def _load(db: Session, rows: list[dict], batch_size: int = LOAD_BATCH_SIZE) -> dict:
    from app.models.listening_history import ListeningHistory

    new_records = 0
    duplicates = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        if not batch:
            continue
        stmt = pg_insert(ListeningHistory).values(batch).on_conflict_do_nothing(
            index_elements=["artist_name", "track_name", "end_time"]
        )
        result = db.execute(stmt)
        inserted = result.rowcount
        new_records += inserted
        duplicates += len(batch) - inserted

    db.commit()
    return {"new_records_loaded": new_records, "duplicate_records_skipped": duplicates}


def _enrich(db: Session) -> dict:
    """
    Enrich listening_history tracks with Spotify metadata + embeddings.
    Non-fatal: any failure is logged and the ETL job result is unaffected.
    Processes at most ENRICH_BATCH_LIMIT tracks per run (oldest unresolved first).
    Returns a summary dict merged into the job result.
    """
    from app.models.track_metadata import TrackMetadata

    try:
        client = get_spotify_client()
        if client is None:
            logger.warning("enrichment: Spotify credentials not configured — skipping")
            return {"tracks_enriched": 0, "enrichment_skipped": True}

        # Count total unresolved, then fetch only the oldest ENRICH_BATCH_LIMIT
        total_unresolved_row = db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT DISTINCT lh.artist_name, lh.track_name
                    FROM listening_history lh
                    LEFT JOIN track_metadata tm
                        ON tm.artist_name = lh.artist_name AND tm.track_name = lh.track_name
                    WHERE tm.artist_name IS NULL
                ) sub
                """
            )
        ).scalar()
        total_unresolved = int(total_unresolved_row or 0)

        if total_unresolved == 0:
            logger.info("enrichment: all tracks already resolved")
            return {"tracks_enriched": 0, "enrichment_skipped": False}

        batch_size = min(total_unresolved, ENRICH_BATCH_LIMIT)
        logger.info("enrichment: enriching %d of %d total unresolved tracks", batch_size, total_unresolved)

        unresolved = db.execute(
            text(
                """
                SELECT DISTINCT lh.artist_name, lh.track_name
                FROM listening_history lh
                LEFT JOIN track_metadata tm
                    ON tm.artist_name = lh.artist_name AND tm.track_name = lh.track_name
                WHERE tm.artist_name IS NULL
                ORDER BY lh.artist_name, lh.track_name
                LIMIT :limit
                """
            ),
            {"limit": batch_size},
        ).fetchall()

        resolved: list[dict] = []
        artist_id_set: set[str] = set()

        for i, (artist_name, track_name) in enumerate(unresolved):
            if i > 0:
                time.sleep(SEARCH_DELAY_SECONDS)

            try:
                match = client.search_track(artist_name, track_name)
            except Exception as exc:
                logger.warning("enrichment: search failed for %r / %r: %s", artist_name, track_name, exc)
                match = None

            row: dict = {
                "artist_name": artist_name,
                "track_name": track_name,
                "spotify_track_id": None,
                "spotify_artist_id": None,
                "genres": [],
            }
            if match:
                row["spotify_track_id"] = match["spotify_track_id"]
                row["spotify_artist_id"] = match["spotify_artist_id"]
                if match["spotify_artist_id"]:
                    artist_id_set.add(match["spotify_artist_id"])
            resolved.append(row)

        # Batch-fetch genres for all unique artist IDs
        genre_map: dict[str, list[str]] = {}
        if artist_id_set:
            try:
                artists = client.get_artists(list(artist_id_set))
                for a in artists:
                    genre_map[a["id"]] = a.get("genres", [])
            except Exception as exc:
                logger.warning("enrichment: artist batch lookup failed: %s", exc)

        for row in resolved:
            if row["spotify_artist_id"]:
                row["genres"] = genre_map.get(row["spotify_artist_id"], [])

        # Generate embeddings for all resolved tracks
        try:
            from worker.ml.embeddings import embed_tracks
            vectors = embed_tracks(resolved)
            for row, vec in zip(resolved, vectors):
                row["embedding"] = vec
        except Exception as exc:
            logger.warning("enrichment: embedding generation failed: %s", exc)
            for row in resolved:
                row.setdefault("embedding", None)

        stmt = pg_insert(TrackMetadata).values(resolved).on_conflict_do_nothing(
            index_elements=["artist_name", "track_name"]
        )
        db.execute(stmt)
        db.commit()

        logger.info("enrichment: inserted %d track_metadata rows", len(resolved))
        return {"tracks_enriched": len(resolved), "enrichment_skipped": False}

    except Exception as exc:
        logger.warning("enrichment: unexpected failure — skipping: %s", exc, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return {"tracks_enriched": 0, "enrichment_skipped": True}


@celery_app.task(name="worker.tasks.etl_pipeline.run_etl_pipeline", bind=True, base=BaseJobTask)
def run_etl_pipeline(self, job_id: str) -> None:
    from app.models.job import Job

    db = get_sync_db()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        source_dir = Path(job.payload.get("source") or DEFAULT_SOURCE_DIR)
        files = _find_source_files(source_dir)
        if not files:
            error_msg = f"No streaming history files found in '{source_dir}'"
            logger.error("job %s etl failed: %s", job_id, error_msg)
            job.status = "failed"
            job.error = error_msg
            job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
            return

        entries = _extract(files)
        rows, filter_stats = _transform(entries)

        db.refresh(job)
        if job.status == "cancelled":
            logger.info("job %s cancelled during execution", job_id)
            return

        load_stats = _load(db, rows)
        enrich_stats = _enrich(db)

        job.result = {
            "status": "success",
            "total_extracted": len(entries),
            "skipped_short_plays": filter_stats["skipped_short_plays"],
            "skipped_invalid": filter_stats["skipped_invalid"],
            "new_records_loaded": load_stats["new_records_loaded"],
            "duplicate_records_skipped": load_stats["duplicate_records_skipped"],
            "source_files": [f.name for f in files],
            "pipeline": "spotify_etl",
            "tracks_enriched": enrich_stats["tracks_enriched"],
            "enrichment_skipped": enrich_stats["enrichment_skipped"],
        }
        job.status = "complete"
        job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.info("job %s complete etl new_records=%d", job_id, load_stats["new_records_loaded"])
    except Exception as exc:
        retry_num = self.request.retries
        countdown = (2 ** retry_num) + random.uniform(0, 0.3 * (2 ** retry_num))
        raise self.retry(exc=exc, countdown=countdown)
    finally:
        db.close()
