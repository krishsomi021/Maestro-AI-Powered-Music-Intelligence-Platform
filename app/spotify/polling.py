"""
Spotify recently-played polling — sync, invoked by the Celery Beat task
(worker/tasks/scheduled.py::poll_recently_played). Uses sync SQLAlchemy/psycopg2,
matching how every other worker task accesses the DB.

ms_played: the recently-played endpoint exposes no real "time actually listened"
field, only the track's full duration_ms. By design, every polled item is stored
using duration_ms as ms_played -- the export ETL's 30s skip-filter does not apply
here since it only makes sense against the export format's real msPlayed field.
"""

import logging
from datetime import datetime, timezone

import requests
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.spotify.oauth import _TOKEN_URL, _parse_token_response

logger = logging.getLogger(__name__)

_RECENTLY_PLAYED_URL = "https://api.spotify.com/v1/me/player/recently-played"
_POLL_LIMIT = 50


def _get_stored_tokens(db: Session) -> dict | None:
    row = db.execute(
        text("SELECT access_token, refresh_token, expires_at FROM spotify_oauth_tokens LIMIT 1")
    ).mappings().first()
    return dict(row) if row else None


def _refresh_if_needed(db: Session, tokens: dict) -> str:
    """Returns a valid access_token, refreshing + persisting in place if the
    stored expires_at (already written with a 60s safety margin) has passed."""
    if datetime.now(timezone.utc).replace(tzinfo=None) < tokens["expires_at"]:
        return tokens["access_token"]

    resp = requests.post(
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": settings.spotify_client_id,
            "client_secret": settings.spotify_client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    resp.raise_for_status()
    refreshed = _parse_token_response(resp.json(), fallback_refresh_token=tokens["refresh_token"])

    db.execute(
        text("""
            UPDATE spotify_oauth_tokens
            SET access_token = :access_token,
                refresh_token = :refresh_token,
                expires_at = :expires_at,
                updated_at = now()
        """),
        refreshed,
    )
    db.commit()
    return refreshed["access_token"]


def _fetch_recently_played(access_token: str) -> list[dict]:
    resp = requests.get(
        _RECENTLY_PLAYED_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        params={"limit": _POLL_LIMIT},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def _map_to_listening_history(items: list[dict]) -> list[dict]:
    rows = []
    for item in items:
        track = item.get("track") or {}
        artists = track.get("artists") or []
        played_at_raw = item.get("played_at")
        if not artists or not track.get("name") or not played_at_raw:
            continue

        end_time = datetime.fromisoformat(played_at_raw.replace("Z", "+00:00")).replace(tzinfo=None)
        rows.append({
            "artist_name": artists[0]["name"],
            "track_name": track["name"],
            "end_time": end_time,
            "ms_played": track.get("duration_ms", 0),
        })
    return rows


def run_recently_played_poll(db: Session) -> dict:
    """Business logic invoked by worker.tasks.scheduled.poll_recently_played.
    Returns {"new_plays": int, "skipped": str | None}.

    Idempotency: there's no Job row here (this does the work directly, it doesn't
    enqueue anything), so the guard is "no tokens stored yet" rather than a
    Job-table lock. Duplicate/overlap safety comes from on_conflict_do_nothing on
    (artist_name, track_name, end_time), the same constraint the export ETL uses.
    """
    tokens = _get_stored_tokens(db)
    if tokens is None:
        logger.info("Spotify not yet authorized -- skipping poll")
        return {"new_plays": 0, "skipped": "not_authorized"}

    access_token = _refresh_if_needed(db, tokens)
    items = _fetch_recently_played(access_token)
    rows = _map_to_listening_history(items)

    if not rows:
        return {"new_plays": 0, "skipped": None}

    from app.models.listening_history import ListeningHistory

    stmt = pg_insert(ListeningHistory).values(rows).on_conflict_do_nothing(
        index_elements=["artist_name", "track_name", "end_time"]
    )
    result = db.execute(stmt)
    db.commit()
    return {"new_plays": result.rowcount, "skipped": None}
