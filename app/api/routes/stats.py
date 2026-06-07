import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dao import stats_dao
from app.db.async_session import get_db
from app.schemas.stats import (
    ListeningTrendItem,
    ListeningTrendsResponse,
    TopArtistItem,
    TopArtistsResponse,
    TopTrackItem,
    TopTracksResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/top-tracks", response_model=TopTracksResponse)
async def get_top_tracks(limit: int = 10, db: AsyncSession = Depends(get_db)):
    rows = await stats_dao.get_top_tracks(db, limit)
    return TopTracksResponse(tracks=[TopTrackItem(**row) for row in rows])


@router.get("/top-artists", response_model=TopArtistsResponse)
async def get_top_artists(limit: int = 10, db: AsyncSession = Depends(get_db)):
    rows = await stats_dao.get_top_artists(db, limit)
    return TopArtistsResponse(artists=[TopArtistItem(**row) for row in rows])


@router.get("/listening-trends", response_model=ListeningTrendsResponse)
async def get_listening_trends(db: AsyncSession = Depends(get_db)):
    rows = await stats_dao.get_listening_trends(db)
    return ListeningTrendsResponse(trends=[ListeningTrendItem(**row) for row in rows])
