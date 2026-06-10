import base64
import logging
import time

import requests

logger = logging.getLogger(__name__)

MAX_RETRY_AFTER_SLEEP = 30  # never sleep longer than this regardless of Spotify's header

_spotify_client: "SpotifyClient | None" = None


def get_spotify_client() -> "SpotifyClient | None":
    """Return the module-level singleton; None if credentials are not configured."""
    global _spotify_client
    if _spotify_client is not None:
        return _spotify_client

    from app.config import settings
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        return None

    _spotify_client = SpotifyClient(settings.spotify_client_id, settings.spotify_client_secret)
    return _spotify_client


class SpotifyClient:
    _BASE = "https://api.spotify.com/v1"
    _TOKEN_URL = "https://accounts.spotify.com/api/token"

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expiry: float = 0.0

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _ensure_token(self) -> None:
        if self._token and time.time() < self._token_expiry:
            return
        credentials = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()
        resp = requests.post(
            self._TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        # 60s safety margin so we never use an about-to-expire token
        self._token_expiry = time.time() + data.get("expires_in", 3600) - 60

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict:
        self._ensure_token()
        url = f"{self._BASE}{path}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {self._token}"},
            params=params,
            timeout=10,
        )
        if resp.status_code == 429:
            raw = int(resp.headers.get("Retry-After", "1"))
            sleep_for = min(raw, MAX_RETRY_AFTER_SLEEP)
            logger.warning(
                "Spotify rate limit; Retry-After=%ds, sleeping %ds (cap=%ds)",
                raw, sleep_for, MAX_RETRY_AFTER_SLEEP,
            )
            time.sleep(sleep_for)
            self._ensure_token()
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                params=params,
                timeout=10,
            )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Domain methods
    # ------------------------------------------------------------------

    def search_track(self, artist: str, track: str) -> dict | None:
        """Return {spotify_track_id, spotify_artist_id} or None if no match."""
        data = self._get(
            "/search",
            params={"q": f"artist:{artist} track:{track}", "type": "track", "limit": 1},
        )
        items = data.get("tracks", {}).get("items", [])
        if not items:
            return None
        item = items[0]
        artist_id = item["artists"][0]["id"] if item.get("artists") else None
        return {"spotify_track_id": item["id"], "spotify_artist_id": artist_id}

    def get_artists(self, artist_ids: list[str]) -> list[dict]:
        """Fetch artist objects in batches of 50. Returns list of {id, genres}."""
        results: list[dict] = []
        for i in range(0, len(artist_ids), 50):
            batch = artist_ids[i : i + 50]
            data = self._get("/artists", params={"ids": ",".join(batch)})
            for artist in data.get("artists") or []:
                if artist:
                    results.append({"id": artist["id"], "genres": artist.get("genres", [])})
        return results
