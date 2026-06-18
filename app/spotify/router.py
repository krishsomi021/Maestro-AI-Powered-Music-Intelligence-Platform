"""
Spotify OAuth router — GET /spotify/auth (redirect to Spotify login) and
GET /spotify/callback (exchange code, persist tokens). One-time interactive flow;
the resulting tokens drive the hourly Celery Beat poll (worker/tasks/scheduled.py).

/auth is gated by HTTP Basic Auth (OAUTH_ADMIN_USER/PASSWORD) -- only the operator
can initiate a flow, and shares credentials with whoever connects next out-of-band.
/callback is NOT Basic Auth gated: it's reached via a cross-origin redirect from
accounts.spotify.com, and browsers aren't reliable about resending a cached
Authorization header across that hop. Its security comes from the CSRF `state`
param instead -- single-use, minted only by an authenticated /auth call, verified
against Redis before any token exchange happens.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.rate_limit import limiter
from app.db.async_session import get_db
from app.spotify.oauth import (
    build_authorize_url,
    consume_oauth_state,
    exchange_code_for_tokens,
    generate_oauth_state,
    store_tokens,
)

router = APIRouter(prefix="/spotify", tags=["spotify"])

_basic_auth = HTTPBasic()


def _verify_admin(credentials: HTTPBasicCredentials = Depends(_basic_auth)) -> None:
    # Compare both fields unconditionally (rather than short-circuiting on the
    # first mismatch) so a failed check doesn't leak which field was wrong via timing.
    user_ok = secrets.compare_digest(credentials.username, settings.oauth_admin_user)
    password_ok = secrets.compare_digest(credentials.password, settings.oauth_admin_password)
    if not (user_ok and password_ok):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


@router.get("/auth")
@limiter.limit("5/minute")
async def spotify_auth(
    request: Request, _: None = Depends(_verify_admin)
) -> RedirectResponse:
    state = await generate_oauth_state()
    return RedirectResponse(url=build_authorize_url(state), status_code=307)


@router.get("/callback")
@limiter.limit("5/minute")
async def spotify_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    if error:
        raise HTTPException(status_code=400, detail=f"Spotify authorization denied: {error}")
    if not state or not await consume_oauth_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' query parameter")

    tokens = await exchange_code_for_tokens(code)
    await store_tokens(db, tokens)
    return {"message": "Spotify account connected successfully."}
