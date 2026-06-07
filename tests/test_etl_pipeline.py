from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.models.listening_history import ListeningHistory


# ---------------------------------------------------------------------------
# Transform — pure filtering logic
# ---------------------------------------------------------------------------

def test_etl_filters_short_plays():
    from worker.tasks.etl_pipeline import _transform

    entries = [
        {"endTime": "2025-06-10 15:29", "artistName": "Arctic Monkeys", "trackName": "505", "msPlayed": 10164},
        {"endTime": "2025-06-10 15:35", "artistName": "Arctic Monkeys", "trackName": "Do I Wanna Know?", "msPlayed": 245000},
        {"endTime": "2025-06-10 15:40", "artistName": "Tame Impala", "trackName": "Feels Like We Only Go Backwards", "msPlayed": 29999},
    ]

    rows, stats = _transform(entries)

    assert len(rows) == 1
    assert rows[0]["track_name"] == "Do I Wanna Know?"
    assert stats["skipped_short_plays"] == 2
    assert stats["skipped_invalid"] == 0


# ---------------------------------------------------------------------------
# Load — idempotency via UNIQUE + ON CONFLICT DO NOTHING (real Postgres)
# ---------------------------------------------------------------------------

def test_etl_idempotent(sync_db_session):
    from worker.tasks.etl_pipeline import _load

    rows = [
        {
            "artist_name": "Arctic Monkeys",
            "track_name": "505",
            "end_time": datetime(2025, 6, 10, 15, 29),
            "ms_played": 200000,
        },
        {
            "artist_name": "Tame Impala",
            "track_name": "Feels Like We Only Go Backwards",
            "end_time": datetime(2025, 6, 10, 16, 0),
            "ms_played": 215000,
        },
    ]

    def row_count():
        return sync_db_session.execute(select(func.count()).select_from(ListeningHistory)).scalar_one()

    baseline = row_count()

    first = _load(sync_db_session, rows)
    assert first["new_records_loaded"] == 2
    assert first["duplicate_records_skipped"] == 0
    assert row_count() == baseline + 2

    second = _load(sync_db_session, rows)
    assert second["new_records_loaded"] == 0
    assert second["duplicate_records_skipped"] > 0
    assert row_count() == baseline + 2


# ---------------------------------------------------------------------------
# Stats endpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stats_top_tracks(client, db_session):
    db_session.add_all([
        ListeningHistory(artist_name="Arctic Monkeys", track_name="505", end_time=datetime(2025, 6, 1, 10, 0), ms_played=200000),
        ListeningHistory(artist_name="Arctic Monkeys", track_name="505", end_time=datetime(2025, 6, 2, 10, 0), ms_played=210000),
        ListeningHistory(artist_name="Tame Impala", track_name="Feels Like We Only Go Backwards", end_time=datetime(2025, 6, 3, 10, 0), ms_played=220000),
    ])
    await db_session.commit()

    resp = await client.get("/stats/top-tracks?limit=10")
    assert resp.status_code == 200
    tracks = resp.json()["tracks"]

    assert tracks[0]["track_name"] == "505"
    assert tracks[0]["artist_name"] == "Arctic Monkeys"
    assert tracks[0]["play_count"] == 2
    assert tracks[0]["total_ms_played"] == 410000


@pytest.mark.asyncio
async def test_stats_top_artists(client, db_session):
    db_session.add_all([
        ListeningHistory(artist_name="Arctic Monkeys", track_name="505", end_time=datetime(2025, 6, 1, 10, 0), ms_played=200000),
        ListeningHistory(artist_name="Arctic Monkeys", track_name="Do I Wanna Know?", end_time=datetime(2025, 6, 2, 10, 0), ms_played=210000),
        ListeningHistory(artist_name="Arctic Monkeys", track_name="505", end_time=datetime(2025, 6, 3, 10, 0), ms_played=190000),
        ListeningHistory(artist_name="Tame Impala", track_name="Feels Like We Only Go Backwards", end_time=datetime(2025, 6, 4, 10, 0), ms_played=220000),
    ])
    await db_session.commit()

    resp = await client.get("/stats/top-artists?limit=10")
    assert resp.status_code == 200
    artists = resp.json()["artists"]

    assert artists[0]["artist_name"] == "Arctic Monkeys"
    assert artists[0]["play_count"] == 3
    assert artists[0]["total_ms_played"] == 600000
    assert artists[0]["unique_tracks"] == 2


@pytest.mark.asyncio
async def test_stats_listening_trends(client, db_session):
    db_session.add_all([
        ListeningHistory(artist_name="Arctic Monkeys", track_name="505", end_time=datetime(2025, 6, 1, 10, 0), ms_played=200000),
        ListeningHistory(artist_name="Arctic Monkeys", track_name="505", end_time=datetime(2025, 6, 15, 10, 0), ms_played=210000),
        ListeningHistory(artist_name="Tame Impala", track_name="Feels Like We Only Go Backwards", end_time=datetime(2025, 7, 1, 10, 0), ms_played=220000),
    ])
    await db_session.commit()

    resp = await client.get("/stats/listening-trends")
    assert resp.status_code == 200
    trends = resp.json()["trends"]

    assert trends == [
        {"month": "2025-06", "play_count": 2, "total_ms_played": 410000},
        {"month": "2025-07", "play_count": 1, "total_ms_played": 220000},
    ]
