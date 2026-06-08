import logging

import joblib

logger = logging.getLogger(__name__)

_RECOMMENDER_MODEL_PATH = "models/spotify_recommender.joblib"

_uri_to_meta: dict | None = None
_load_attempted = False


def _load_uri_to_meta() -> dict:
    global _uri_to_meta, _load_attempted
    if _uri_to_meta is None and not _load_attempted:
        _load_attempted = True
        try:
            model = joblib.load(_RECOMMENDER_MODEL_PATH)
            _uri_to_meta = model["uri_to_meta"]
        except FileNotFoundError:
            logger.warning(
                "recommender model not found at %s — track search will return no results",
                _RECOMMENDER_MODEL_PATH,
            )
            _uri_to_meta = {}
    return _uri_to_meta


def search_tracks(query: str, limit: int = 20) -> list[dict]:
    uri_to_meta = _load_uri_to_meta()
    needle = query.strip().lower()
    if not needle:
        return []

    matches = []
    for uri, meta in uri_to_meta.items():
        track_name = meta.get("track_name", "")
        artist_name = meta.get("artist_name", "")
        if needle in track_name.lower() or needle in artist_name.lower():
            matches.append({"uri": uri, "track_name": track_name, "artist_name": artist_name})
            if len(matches) >= limit:
                break
    return matches
