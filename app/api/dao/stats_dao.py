from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listening_history import ListeningHistory


async def get_top_tracks(db: AsyncSession, limit: int = 10) -> list[dict]:
    stmt = (
        select(
            ListeningHistory.track_name,
            ListeningHistory.artist_name,
            func.count().label("play_count"),
            func.sum(ListeningHistory.ms_played).label("total_ms_played"),
        )
        .group_by(ListeningHistory.track_name, ListeningHistory.artist_name)
        .order_by(func.count().desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [dict(row._mapping) for row in result.all()]


async def get_top_artists(db: AsyncSession, limit: int = 10) -> list[dict]:
    stmt = (
        select(
            ListeningHistory.artist_name,
            func.count().label("play_count"),
            func.sum(ListeningHistory.ms_played).label("total_ms_played"),
            func.count(func.distinct(ListeningHistory.track_name)).label("unique_tracks"),
        )
        .group_by(ListeningHistory.artist_name)
        .order_by(func.count().desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [dict(row._mapping) for row in result.all()]


async def get_listening_trends(db: AsyncSession) -> list[dict]:
    month = func.to_char(ListeningHistory.end_time, "YYYY-MM").label("month")
    stmt = (
        select(
            month,
            func.count().label("play_count"),
            func.sum(ListeningHistory.ms_played).label("total_ms_played"),
        )
        .group_by(month)
        .order_by(month)
    )
    result = await db.execute(stmt)
    return [dict(row._mapping) for row in result.all()]
