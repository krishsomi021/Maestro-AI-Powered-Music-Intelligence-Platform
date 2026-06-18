"""
Spotify OAuth (Authorization Code flow) — async, runs in the FastAPI process.

Separate from worker/clients/spotify.py, which is a Client Credentials client used
for public metadata enrichment. This module handles the one-time user login and
token exchange for the real-time listening-data sync pipeline. Tokens are persisted
to Postgres (spotify_oauth_tokens), never to .env or source.

_parse_token_response is shared (imported) by app/spotify/polling.py, which performs
the sync-side token refresh from inside the Celery Beat task.
"""

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import redis.asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SCOPE = "user-read-recently-played"
_TOKEN_SAFETY_MARGIN_SECONDS = 60  # matches worker/clients/spotify.py's convention

_OAUTH_STATE_TTL_SECONDS = 300
_OAUTH_STATE_KEY_PREFIX = "spotify:oauth_state:"


def build_authorize_url(state: str) -> str:
    """Returns the full Spotify authorize URL for a 307 redirect."""
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.spotify_redirect_uri,
        "scope": _SCOPE,
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


async def generate_oauth_state() -> str:
    """Mints a single-use CSRF state token and stores it in Redis with a 5-minute
    TTL -- same Redis connection pattern as app/agent/memory.py::RedisMemoryManager."""
    state = secrets.token_urlsafe(32)
    redis_client = redis.asyncio.from_url(settings.celery_broker_url, decode_responses=True)
    try:
        await redis_client.setex(_OAUTH_STATE_KEY_PREFIX + state, _OAUTH_STATE_TTL_SECONDS, "1")
    finally:
        await redis_client.aclose()
    return state


async def consume_oauth_state(state: str) -> bool:
    """Atomically checks and deletes the state key so it can only be used once --
    avoids the race window a separate GET-then-DELETE would have."""
    redis_client = redis.asyncio.from_url(settings.celery_broker_url, decode_responses=True)
    try:
        deleted = await redis_client.delete(_OAUTH_STATE_KEY_PREFIX + state)
    finally:
        await redis_client.aclose()
    return deleted > 0


def _parse_token_response(data: dict, fallback_refresh_token: str) -> dict:
    """Spotify omits refresh_token on refresh-grant responses -- falls back to the
    previously stored value when absent. expires_at is stored as a naive UTC
    datetime (matching the listening_history.end_time / Job.created_at convention)
    with the same 60s safety margin used by worker/clients/spotify.py."""
    if not data.get("access_token"):
        raise ValueError("Spotify token response missing access_token")

    expires_in = data.get("expires_in", 3600)
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=expires_in - _TOKEN_SAFETY_MARGIN_SECONDS
    )
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token") or fallback_refresh_token,
        "expires_at": expires_at,
    }


async def exchange_code_for_tokens(code: str) -> dict:
    """POST grant_type=authorization_code. Raises httpx.HTTPStatusError on failure."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.spotify_redirect_uri,
                "client_id": settings.spotify_client_id,
                "client_secret": settings.spotify_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()

    tokens = _parse_token_response(resp.json(), fallback_refresh_token="")
    if not tokens["refresh_token"]:
        # Spotify always returns refresh_token on the authorization_code grant;
        # an empty value here would silently break every future refresh.
        raise ValueError("Spotify token response missing refresh_token")
    return tokens


async def store_tokens(db: AsyncSession, tokens: dict) -> None:
    """spotify_oauth_tokens is a single-row table with no natural unique key besides
    the surrogate id, so this updates the existing row in place, or inserts the
    first row if none exists yet."""
    result = await db.execute(
        text("""
            UPDATE spotify_oauth_tokens
            SET access_token = :access_token,
                refresh_token = :refresh_token,
                expires_at = :expires_at,
                updated_at = now()
        """),
        tokens,
    )
    if result.rowcount == 0:
        await db.execute(
            text("""
                INSERT INTO spotify_oauth_tokens (access_token, refresh_token, expires_at)
                VALUES (:access_token, :refresh_token, :expires_at)
            """),
            tokens,
        )
    await db.commit()
