import logging
import time

import requests

logger = logging.getLogger(__name__)

_MIN_REQUEST_INTERVAL_SECONDS = 1.0 / 5.0  # Last.fm: stay under 5 req/sec

_lastfm_client: "LastfmClient | None" = None


def get_lastfm_client() -> "LastfmClient | None":
    """Return the module-level singleton; None if the API key is not configured."""
    global _lastfm_client
    if _lastfm_client is not None:
        return _lastfm_client

    from app.config import settings
    if not settings.lastfm_api_key:
        return None

    _lastfm_client = LastfmClient(settings.lastfm_api_key)
    return _lastfm_client


class LastfmClient:
    _BASE = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._last_request_at: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait_for = _MIN_REQUEST_INTERVAL_SECONDS - elapsed
        if wait_for > 0:
            time.sleep(wait_for)

    def _get(self, method: str, params: dict) -> dict:
        self._throttle()
        resp = requests.get(
            self._BASE,
            params={
                "method": method,
                "api_key": self._api_key,
                "format": "json",
                **params,
            },
            timeout=10,
        )
        self._last_request_at = time.monotonic()
        resp.raise_for_status()
        return resp.json()

    def get_similar_tracks(self, artist: str, track: str, limit: int = 50) -> list[dict]:
        """Return [{artist_name, track_name, match, url}, ...] via track.getSimilar."""
        data = self._get(
            "track.getSimilar",
            {"artist": artist, "track": track, "limit": limit, "autocorrect": 1},
        )
        items = data.get("similartracks", {}).get("track", [])
        results = []
        for item in items:
            try:
                results.append({
                    "artist_name": item["artist"]["name"],
                    "track_name": item["name"],
                    "match": float(item["match"]),
                    "url": item.get("url"),
                })
            except (KeyError, TypeError, ValueError):
                logger.warning("lastfm: skipping malformed getSimilar item: %r", item)
        return results

    def get_top_tags(self, artist: str, track: str, limit: int = 5) -> list[str]:
        """Return up to `limit` tag names via track.getTopTags."""
        data = self._get(
            "track.getTopTags",
            {"artist": artist, "track": track, "autocorrect": 1},
        )
        tags = data.get("toptags", {}).get("tag", [])
        return [t["name"] for t in tags[:limit] if t.get("name")]
