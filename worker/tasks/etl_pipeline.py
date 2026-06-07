import json
import logging
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from worker.celery_app import celery_app
from worker.db.sync_session import get_sync_db
from worker.tasks.base import BaseJobTask

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_DIR = "data/spotify_export"
SOURCE_FILE_GLOB = "StreamingHistory_music_*.json"
END_TIME_FORMAT = "%Y-%m-%d %H:%M"
MIN_MS_PLAYED = 30000
LOAD_BATCH_SIZE = 500


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

        job.result = {
            "status": "success",
            "total_extracted": len(entries),
            "skipped_short_plays": filter_stats["skipped_short_plays"],
            "skipped_invalid": filter_stats["skipped_invalid"],
            "new_records_loaded": load_stats["new_records_loaded"],
            "duplicate_records_skipped": load_stats["duplicate_records_skipped"],
            "source_files": [f.name for f in files],
            "pipeline": "spotify_etl",
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
