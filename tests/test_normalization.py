"""
Verifies app.core.normalization.normalize_key() stays byte-for-byte equivalent to
the raw regexp_replace SQL expression used in migrations/manual/backfill_listening_history_normalized_key.sql
and migrations/init.sql's fresh-install path. If someone changes one without the
other, this test fails instead of relying on a comment.
"""
from sqlalchemy import text

from app.core.normalization import normalize_key

TRICKY_CASES = [
    ("Sigur Rós", "Svefn-g-englar"),
    ("Beyoncé", "XO"),
    ("  Radiohead  ", "  Creep  "),
    ("Arctic Monkeys", "505 (Live)"),
    ("Mötley Crüe", "Kickstart My Heart"),
    ("Björk", "Army of Me (Remix)"),
    ("A$AP Rocky", "L$D"),
    ("Sufjan Stevens", "Chicago -- Original Version"),
    ("  Multiple   Spaces  ", "Track   Name"),
    ("UPPERCASE ARTIST", "lowercase track"),
]


def _sql_normalize(db, artist: str, track: str) -> str:
    row = db.execute(
        text(
            """
            SELECT
                regexp_replace(lower(trim(:artist)), '[^a-z0-9]', '', 'g')
                || '::' ||
                regexp_replace(lower(trim(:track)), '[^a-z0-9]', '', 'g')
            """
        ),
        {"artist": artist, "track": track},
    ).scalar()
    return row


class TestNormalizationMatchesSql:
    def test_python_matches_sql_for_tricky_inputs(self, sync_db_session):
        for artist, track in TRICKY_CASES:
            python_result = normalize_key(artist, track)
            sql_result = _sql_normalize(sync_db_session, artist, track)
            assert python_result == sql_result, (
                f"normalize_key drift for ({artist!r}, {track!r}): "
                f"python={python_result!r} sql={sql_result!r}"
            )


class TestNormalizationBehavior:
    def test_case_insensitive(self):
        assert normalize_key("Drake", "God's Plan") == normalize_key("DRAKE", "god's plan")

    def test_whitespace_trimmed(self):
        assert normalize_key("Drake", "God's Plan") == normalize_key("  Drake  ", "  God's Plan  ")

    def test_punctuation_stripped(self):
        assert normalize_key("Drake", "Gods Plan") == normalize_key("Drake", "God's Plan")

    def test_different_tracks_produce_different_keys(self):
        assert normalize_key("Drake", "God's Plan") != normalize_key("Drake", "Hotline Bling")
