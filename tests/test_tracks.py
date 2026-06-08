from unittest.mock import patch

import pytest

from app.api.services import track_search_service

SAMPLE_URI_TO_META = {
    "spotify:track:aaa": {"track_name": "Midnight City", "artist_name": "M83"},
    "spotify:track:bbb": {"track_name": "Strobe", "artist_name": "Deadmau5"},
    "spotify:track:ccc": {"track_name": "Starboy", "artist_name": "The Weeknd"},
}


@pytest.fixture(autouse=True)
def reset_track_search_cache():
    track_search_service._uri_to_meta = None
    track_search_service._load_attempted = False
    yield
    track_search_service._uri_to_meta = None
    track_search_service._load_attempted = False


def test_search_tracks_matches_by_track_name():
    with patch("joblib.load", return_value={"uri_to_meta": SAMPLE_URI_TO_META}):
        results = track_search_service.search_tracks("star")

    uris = {r["uri"] for r in results}
    assert uris == {"spotify:track:ccc"}


def test_search_tracks_matches_by_artist_name():
    with patch("joblib.load", return_value={"uri_to_meta": SAMPLE_URI_TO_META}):
        results = track_search_service.search_tracks("weeknd")

    assert len(results) == 1
    assert results[0]["uri"] == "spotify:track:ccc"


def test_search_tracks_empty_query_returns_nothing():
    with patch("joblib.load", return_value={"uri_to_meta": SAMPLE_URI_TO_META}):
        assert track_search_service.search_tracks("") == []


def test_search_tracks_missing_model_returns_empty_list():
    with patch("joblib.load", side_effect=FileNotFoundError):
        results = track_search_service.search_tracks("midnight")

    assert results == []


@pytest.mark.asyncio
async def test_search_tracks_route(client):
    with patch("joblib.load", return_value={"uri_to_meta": SAMPLE_URI_TO_META}):
        response = await client.get("/tracks/search", params={"q": "midnight"})

    assert response.status_code == 200
    body = response.json()
    assert body["tracks"] == [
        {"uri": "spotify:track:aaa", "track_name": "Midnight City", "artist_name": "M83"}
    ]
