"""
Tests for Stage 1 catalog expansion (worker/tasks/expand_catalog.py).
Never hits the live Last.fm API — the client is always mocked.
"""
from unittest.mock import MagicMock

from worker.tasks.expand_catalog import _generate_candidates, MIN_MATCH_SCORE


def _make_db(seed_rows, history_keys):
    """First execute() call returns seeds, second returns existing history keys."""
    db = MagicMock()
    seeds_result = MagicMock()
    seeds_result.fetchall.return_value = seed_rows
    history_result = MagicMock()
    history_result.fetchall.return_value = [(k,) for k in history_keys]
    db.execute.side_effect = [seeds_result, history_result]
    return db


def _make_client(similar_by_seed=None, tags_by_seed=None):
    client = MagicMock()
    similar_by_seed = similar_by_seed or {}
    tags_by_seed = tags_by_seed or {}

    def get_similar_tracks(artist, track, limit=50):
        return similar_by_seed.get((artist, track), [])

    def get_top_tags(artist, track, limit=5):
        return tags_by_seed.get((artist, track), [])

    client.get_similar_tracks.side_effect = get_similar_tracks
    client.get_top_tags.side_effect = get_top_tags
    return client


class TestThresholdFiltering:
    def test_below_threshold_candidates_dropped(self):
        seed_rows = [("Radiohead", "Creep", 42)]
        db = _make_db(seed_rows, history_keys=[])
        client = _make_client(
            similar_by_seed={
                ("Radiohead", "Creep"): [
                    {"artist_name": "Muse", "track_name": "Hysteria", "match": 0.5, "url": "u1"},
                    {"artist_name": "Nowhere", "track_name": "Noise", "match": 0.05, "url": "u2"},
                ]
            },
        )
        candidates = _generate_candidates(db, client)
        names = {c["track_name"] for c in candidates}
        assert "Hysteria" in names
        assert "Noise" not in names

    def test_exactly_at_threshold_is_kept(self):
        seed_rows = [("Radiohead", "Creep", 42)]
        db = _make_db(seed_rows, history_keys=[])
        client = _make_client(
            similar_by_seed={
                ("Radiohead", "Creep"): [
                    {"artist_name": "Muse", "track_name": "Hysteria", "match": MIN_MATCH_SCORE, "url": "u1"},
                ]
            },
        )
        candidates = _generate_candidates(db, client)
        assert len(candidates) == 1


class TestDedupAgainstHistory:
    def test_candidate_already_in_history_dropped(self):
        seed_rows = [("Radiohead", "Creep", 42)]
        db = _make_db(seed_rows, history_keys=["muse::hysteria"])
        client = _make_client(
            similar_by_seed={
                ("Radiohead", "Creep"): [
                    {"artist_name": "Muse", "track_name": "Hysteria", "match": 0.9, "url": "u1"},
                    {"artist_name": "Muse", "track_name": "Starlight", "match": 0.8, "url": "u2"},
                ]
            },
        )
        candidates = _generate_candidates(db, client)
        names = {c["track_name"] for c in candidates}
        assert "Hysteria" not in names
        assert "Starlight" in names

    def test_dedup_across_seeds_keeps_higher_score(self):
        seed_rows = [("Radiohead", "Creep", 42), ("Muse", "Plug In Baby", 30)]
        db = _make_db(seed_rows, history_keys=[])
        client = _make_client(
            similar_by_seed={
                ("Radiohead", "Creep"): [
                    {"artist_name": "Muse", "track_name": "Hysteria", "match": 0.4, "url": "u1"},
                ],
                ("Muse", "Plug In Baby"): [
                    {"artist_name": "Muse", "track_name": "Hysteria", "match": 0.9, "url": "u1"},
                ],
            },
        )
        candidates = _generate_candidates(db, client)
        matching = [c for c in candidates if c["track_name"] == "Hysteria"]
        assert len(matching) == 1
        assert matching[0]["lastfm_match_score"] == 0.9


class TestSeedTagsFoldedIntoCandidates:
    def test_seed_tags_applied_to_its_candidates_not_others(self):
        seed_rows = [("Radiohead", "Creep", 42), ("Drake", "God's Plan", 30)]
        db = _make_db(seed_rows, history_keys=[])
        client = _make_client(
            similar_by_seed={
                ("Radiohead", "Creep"): [
                    {"artist_name": "Muse", "track_name": "Hysteria", "match": 0.5, "url": "u1"},
                ],
                ("Drake", "God's Plan"): [
                    {"artist_name": "Future", "track_name": "Mask Off", "match": 0.5, "url": "u2"},
                ],
            },
            tags_by_seed={
                ("Radiohead", "Creep"): ["alternative rock", "90s"],
                ("Drake", "God's Plan"): ["hip hop", "rap"],
            },
        )
        candidates = _generate_candidates(db, client)
        by_name = {c["track_name"]: c for c in candidates}
        assert by_name["Hysteria"]["tags"] == ["alternative rock", "90s"]
        assert by_name["Mask Off"]["tags"] == ["hip hop", "rap"]

    def test_get_top_tags_called_once_per_seed_not_per_candidate(self):
        seed_rows = [("Radiohead", "Creep", 42)]
        db = _make_db(seed_rows, history_keys=[])
        client = _make_client(
            similar_by_seed={
                ("Radiohead", "Creep"): [
                    {"artist_name": "Muse", "track_name": "Hysteria", "match": 0.5, "url": "u1"},
                    {"artist_name": "Coldplay", "track_name": "Yellow", "match": 0.4, "url": "u2"},
                    {"artist_name": "U2", "track_name": "One", "match": 0.3, "url": "u3"},
                ]
            },
        )
        _generate_candidates(db, client)
        assert client.get_top_tags.call_count == 1


class TestNoSeeds:
    def test_empty_history_returns_no_candidates(self):
        db = _make_db(seed_rows=[], history_keys=[])
        client = _make_client()
        candidates = _generate_candidates(db, client)
        assert candidates == []
