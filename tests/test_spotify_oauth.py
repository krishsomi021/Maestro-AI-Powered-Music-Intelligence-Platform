"""
Tests for the Spotify OAuth + recently-played polling feature.
All external Spotify HTTP calls are mocked -- no real API key is required.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text


# ---------------------------------------------------------------------------
# _parse_token_response — pure logic
# ---------------------------------------------------------------------------

class TestParseTokenResponse:
    def test_parses_access_refresh_and_expiry(self):
        from app.spotify.oauth import _parse_token_response

        before = datetime.now(timezone.utc).replace(tzinfo=None)
        tokens = _parse_token_response(
            {"access_token": "a1", "refresh_token": "r1", "expires_in": 3600},
            fallback_refresh_token="",
        )

        assert tokens["access_token"] == "a1"
        assert tokens["refresh_token"] == "r1"
        # expires_at = now + expires_in - 60s safety margin
        expected = before + timedelta(seconds=3600 - 60)
        assert abs((tokens["expires_at"] - expected).total_seconds()) < 5

    def test_missing_refresh_token_falls_back(self):
        from app.spotify.oauth import _parse_token_response

        tokens = _parse_token_response(
            {"access_token": "a1", "expires_in": 3600},
            fallback_refresh_token="old-refresh",
        )
        assert tokens["refresh_token"] == "old-refresh"

    def test_missing_access_token_raises(self):
        from app.spotify.oauth import _parse_token_response

        with pytest.raises(ValueError):
            _parse_token_response({"expires_in": 3600}, fallback_refresh_token="old")


# ---------------------------------------------------------------------------
# exchange_code_for_tokens — async, mocked httpx
# ---------------------------------------------------------------------------

class TestExchangeCodeForTokens:
    @pytest.mark.asyncio
    async def test_exchange_posts_authorization_code_grant(self):
        from app.spotify.oauth import exchange_code_for_tokens

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "a1",
            "refresh_token": "r1",
            "expires_in": 3600,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.spotify.oauth.httpx.AsyncClient", return_value=mock_client):
            tokens = await exchange_code_for_tokens("some-code")

        assert tokens["access_token"] == "a1"
        assert tokens["refresh_token"] == "r1"
        _, kwargs = mock_client.post.call_args
        assert kwargs["data"]["grant_type"] == "authorization_code"
        assert kwargs["data"]["code"] == "some-code"

    @pytest.mark.asyncio
    async def test_exchange_raises_when_refresh_token_missing(self):
        from app.spotify.oauth import exchange_code_for_tokens

        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "a1", "expires_in": 3600}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.spotify.oauth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ValueError):
                await exchange_code_for_tokens("some-code")


# ---------------------------------------------------------------------------
# _refresh_if_needed — sync, real DB row, mocked requests
# ---------------------------------------------------------------------------

class TestRefreshIfNeeded:
    def test_skips_refresh_when_token_still_valid(self, sync_db_session):
        from app.spotify.polling import _refresh_if_needed

        tokens = {
            "access_token": "still-valid",
            "refresh_token": "r1",
            "expires_at": datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        }
        with patch("app.spotify.polling.requests.post") as mock_post:
            access_token = _refresh_if_needed(sync_db_session, tokens)

        assert access_token == "still-valid"
        mock_post.assert_not_called()

    def test_refreshes_when_token_expired(self, sync_db_session, clean_scheduled_spotify_tokens):
        from app.spotify.polling import _refresh_if_needed

        sync_db_session.execute(
            text("""
                INSERT INTO spotify_oauth_tokens (access_token, refresh_token, expires_at)
                VALUES ('old-token', 'r1', :expires_at)
            """),
            {"expires_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)},
        )
        sync_db_session.commit()

        tokens = {
            "access_token": "old-token",
            "refresh_token": "r1",
            "expires_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5),
        }

        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "new-token", "expires_in": 3600}
        mock_response.raise_for_status = MagicMock()

        with patch("app.spotify.polling.requests.post", return_value=mock_response) as mock_post:
            access_token = _refresh_if_needed(sync_db_session, tokens)

        mock_post.assert_called_once()
        assert access_token == "new-token"

        row = sync_db_session.execute(
            text("SELECT access_token, refresh_token FROM spotify_oauth_tokens LIMIT 1")
        ).mappings().first()
        assert row["access_token"] == "new-token"
        assert row["refresh_token"] == "r1"  # refresh_token absent from response -> falls back


# ---------------------------------------------------------------------------
# run_recently_played_poll — upsert idempotency, real DB
# ---------------------------------------------------------------------------

class TestRunRecentlyPlayedPoll:
    def test_skips_when_not_authorized(self, sync_db_session, clean_scheduled_spotify_tokens):
        from app.spotify.polling import run_recently_played_poll

        result = run_recently_played_poll(sync_db_session)
        assert result == {"new_plays": 0, "skipped": "not_authorized"}

    def test_upsert_skips_duplicate_row(self, sync_db_session, clean_scheduled_spotify_tokens):
        from app.spotify.polling import run_recently_played_poll

        sync_db_session.execute(
            text("""
                INSERT INTO spotify_oauth_tokens (access_token, refresh_token, expires_at)
                VALUES ('valid-token', 'r1', :expires_at)
            """),
            {"expires_at": datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)},
        )
        sync_db_session.commit()

        end_time_str = "2025-06-10T15:29:00.000Z"
        sync_db_session.execute(
            text("""
                INSERT INTO listening_history (artist_name, track_name, end_time, ms_played)
                VALUES ('Arctic Monkeys', '505', '2025-06-10 15:29:00', 200000)
            """)
        )
        sync_db_session.commit()

        recently_played_item = {
            "track": {
                "name": "505",
                "duration_ms": 213000,
                "artists": [{"name": "Arctic Monkeys"}],
            },
            "played_at": end_time_str,
        }

        with patch(
            "app.spotify.polling._fetch_recently_played", return_value=[recently_played_item]
        ):
            result = run_recently_played_poll(sync_db_session)

        assert result == {"new_plays": 0, "skipped": None}

    def test_upsert_inserts_new_row(self, sync_db_session, clean_scheduled_spotify_tokens):
        from app.spotify.polling import run_recently_played_poll

        sync_db_session.execute(
            text("""
                INSERT INTO spotify_oauth_tokens (access_token, refresh_token, expires_at)
                VALUES ('valid-token', 'r1', :expires_at)
            """),
            {"expires_at": datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)},
        )
        sync_db_session.commit()

        recently_played_item = {
            "track": {
                "name": "Brand New Track",
                "duration_ms": 180000,
                "artists": [{"name": "Some Artist"}],
            },
            "played_at": "2030-01-01T00:00:00.000Z",
        }

        with patch(
            "app.spotify.polling._fetch_recently_played", return_value=[recently_played_item]
        ):
            result = run_recently_played_poll(sync_db_session)

        assert result == {"new_plays": 1, "skipped": None}

        row = sync_db_session.execute(
            text("""
                SELECT ms_played FROM listening_history
                WHERE artist_name = 'Some Artist' AND track_name = 'Brand New Track'
            """)
        ).mappings().first()
        assert row["ms_played"] == 180000


# ---------------------------------------------------------------------------
# GET /spotify/auth — redirect
# ---------------------------------------------------------------------------

class TestSpotifyAuthRedirect:
    @pytest.mark.asyncio
    async def test_auth_redirects_to_spotify_domain(self, client):
        response = await client.get("/spotify/auth", follow_redirects=False)

        assert response.status_code == 307
        assert "accounts.spotify.com" in response.headers["location"]
        assert "user-read-recently-played" in response.headers["location"]
