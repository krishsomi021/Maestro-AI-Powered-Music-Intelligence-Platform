import logging

from fastapi import APIRouter

from app.api.services import track_search_service
from app.schemas.track import TrackSearchResponse, TrackSearchResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tracks", tags=["tracks"])


@router.get("/search", response_model=TrackSearchResponse)
async def search_tracks(q: str = "", limit: int = 20):
    results = track_search_service.search_tracks(q, limit)
    return TrackSearchResponse(tracks=[TrackSearchResult(**r) for r in results])
