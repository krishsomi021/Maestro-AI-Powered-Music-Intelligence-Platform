import re

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def _clean(value: str) -> str:
    return _NON_ALNUM_RE.sub("", value.strip().lower())


def normalize_key(artist_name: str, track_name: str) -> str:
    """
    Canonical artist::track dedup key shared by the ETL load path
    (listening_history.normalized_key) and the catalog expansion task
    (external_catalog.normalized_key), so both sides of the anti-join
    in discovery-mode recommendations agree on identity.

    Must stay equivalent to the raw-SQL backfill expression in
    migrations/manual_patches/001_expand_catalog.sql —
    see tests/test_normalization.py, which asserts this against the
    live SQL expression rather than just documenting the invariant.
    """
    return f"{_clean(artist_name)}::{_clean(track_name)}"
