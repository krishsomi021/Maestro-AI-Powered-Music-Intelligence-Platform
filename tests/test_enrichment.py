"""
Tests for the ETL enrichment phase (_enrich).
Spotify client is always mocked — never hits the live API.
"""
from unittest.mock import MagicMock, call, patch

import pytest


def _make_mock_db(total_count=0, pairs=None):
    """
    Return a mock Session configured for _enrich's two-query pattern:
    first execute() → scalar() returns total_count,
    second execute() → fetchall() returns pairs.
    """
    db = MagicMock()
    count_result = MagicMock()
    count_result.scalar.return_value = total_count

    pair_result = MagicMock()
    pair_result.fetchall.return_value = pairs if pairs is not None else []

    db.execute.side_effect = [count_result, pair_result]
    return db


class TestEnrichmentSkipsAlreadyResolved:
    def test_zero_unresolved_returns_early(self):
        from worker.tasks.etl_pipeline import _enrich

        db = _make_mock_db(total_count=0)
        with patch("worker.tasks.etl_pipeline.get_spotify_client") as mock_factory:
            mock_client = MagicMock()
            mock_factory.return_value = mock_client
            result = _enrich(db)

        assert result == {"tracks_enriched": 0, "enrichment_skipped": False}
        mock_client.search_track.assert_not_called()


class TestEnrichmentBatchCap:
    def test_batch_limit_passed_to_query(self):
        """When there are more unresolved tracks than the cap, only ENRICH_BATCH_LIMIT are fetched."""
        from worker.tasks.etl_pipeline import _enrich, ENRICH_BATCH_LIMIT

        total = ENRICH_BATCH_LIMIT + 50
        db = _make_mock_db(total_count=total, pairs=[])

        with patch("worker.tasks.etl_pipeline.get_spotify_client") as mock_factory:
            mock_factory.return_value = MagicMock()
            _enrich(db)

        # The second execute call should pass ENRICH_BATCH_LIMIT as the limit param
        second_call_kwargs = db.execute.call_args_list[1]
        params = second_call_kwargs[0][1] if len(second_call_kwargs[0]) > 1 else second_call_kwargs[1].get("params", {})
        # params is the second positional arg to db.execute(text(...), params)
        assert second_call_kwargs[0][1]["limit"] == ENRICH_BATCH_LIMIT

    def test_log_message_shows_batch_of_total(self, caplog):
        """Log line shows 'enriching X of Y total unresolved tracks'."""
        import logging
        from worker.tasks.etl_pipeline import _enrich, ENRICH_BATCH_LIMIT

        total = ENRICH_BATCH_LIMIT + 100
        db = _make_mock_db(total_count=total, pairs=[("Artist", "Track")])

        with patch("worker.tasks.etl_pipeline.get_spotify_client") as mock_factory:
            mock_client = MagicMock()
            mock_client.search_track.return_value = None
            mock_client.get_artists.return_value = []
            mock_factory.return_value = mock_client
            with patch("worker.tasks.etl_pipeline.pg_insert"):
                with caplog.at_level(logging.INFO, logger="worker.tasks.etl_pipeline"):
                    _enrich(db)

        assert any(
            f"enriching {ENRICH_BATCH_LIMIT} of {total} total unresolved tracks" in r.message
            for r in caplog.records
        )


class TestSearchDelay:
    def test_delay_called_between_tracks(self):
        """time.sleep(SEARCH_DELAY_SECONDS) must be called N-1 times for N tracks."""
        from worker.tasks.etl_pipeline import _enrich, SEARCH_DELAY_SECONDS

        pairs = [("Artist A", "Track 1"), ("Artist B", "Track 2"), ("Artist C", "Track 3")]
        db = _make_mock_db(total_count=len(pairs), pairs=pairs)

        with patch("worker.tasks.etl_pipeline.get_spotify_client") as mock_factory, \
             patch("worker.tasks.etl_pipeline.time") as mock_time, \
             patch("worker.tasks.etl_pipeline.pg_insert"):
            mock_client = MagicMock()
            mock_client.search_track.return_value = None
            mock_client.get_artists.return_value = []
            mock_factory.return_value = mock_client
            _enrich(db)

        sleep_calls = [c for c in mock_time.sleep.call_args_list]
        assert len(sleep_calls) == len(pairs) - 1
        for c in sleep_calls:
            assert c[0][0] == SEARCH_DELAY_SECONDS


class TestEnrichmentNonFatal:
    def test_api_failure_does_not_propagate(self):
        """If SpotifyClient.search_track raises, enrichment completes without re-raising."""
        import requests
        from worker.tasks.etl_pipeline import _enrich

        db = _make_mock_db(total_count=1, pairs=[("Arctic Monkeys", "505")])

        with patch("worker.tasks.etl_pipeline.get_spotify_client") as mock_factory, \
             patch("worker.tasks.etl_pipeline.time"), \
             patch("worker.tasks.etl_pipeline.pg_insert"):
            mock_client = MagicMock()
            mock_client.search_track.side_effect = requests.HTTPError("500 Server Error")
            mock_client.get_artists.return_value = []
            mock_factory.return_value = mock_client
            result = _enrich(db)

        assert isinstance(result, dict)
        assert "tracks_enriched" in result

    def test_outer_exception_returns_skipped(self):
        """A DB-level error returns enrichment_skipped=True without propagating."""
        from worker.tasks.etl_pipeline import _enrich

        db = MagicMock()
        db.execute.side_effect = Exception("DB is on fire")

        with patch("worker.tasks.etl_pipeline.get_spotify_client") as mock_factory:
            mock_factory.return_value = MagicMock()
            result = _enrich(db)

        assert result["enrichment_skipped"] is True
        assert result["tracks_enriched"] == 0


class TestEnrichmentSkipsWithoutCredentials:
    def test_no_credentials_returns_skipped(self):
        from worker.tasks.etl_pipeline import _enrich

        db = MagicMock()
        with patch("worker.tasks.etl_pipeline.get_spotify_client", return_value=None):
            result = _enrich(db)

        assert result == {"tracks_enriched": 0, "enrichment_skipped": True}
        db.execute.assert_not_called()
