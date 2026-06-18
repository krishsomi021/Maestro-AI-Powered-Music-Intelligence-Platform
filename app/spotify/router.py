"""
Spotify OAuth router — GET /spotify/auth (redirect to Spotify login) and
GET /spotify/callback (exchange code, persist tokens). One-time interactive flow;
the resulting tokens drive the hourly Celery Beat poll (worker/tasks/scheduled.py).
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.async_session import get_db
from app.spotify.oauth import build_authorize_url, exchange_code_for_tokens, store_tokens

router = APIRouter(prefix="/spotify", tags=["spotify"])


@router.get("/auth")
async def spotify_auth() -> RedirectResponse:
    return RedirectResponse(url=build_authorize_url(), status_code=307)


@router.get("/callback")
async def spotify_callback(
    code: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    if error:
        raise HTTPException(status_code=400, detail=f"Spotify authorization denied: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' query parameter")

    tokens = await exchange_code_for_tokens(code)
    await store_tokens(db, tokens)
    return {"message": "Spotify account connected successfully."}
