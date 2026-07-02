-- Manual patch for an ALREADY-RUNNING database (i.e. one whose Postgres data
-- volume was initialized before this change landed in migrations/init.sql).
--
-- docker-entrypoint-initdb.d scripts (init.sql, 002_agent_schema.sql) only run
-- once, against an empty volume — see docker-compose.yml. Any schema change
-- added to init.sql after a volume already exists needs its own entry here,
-- numbered in the order it was introduced. Everything below is idempotent
-- (IF NOT EXISTS / WHERE ... IS NULL guards), so it's safe to re-run.
--
-- Run once by hand:
--   docker compose exec -T postgres psql -U <user> -d <db> \
--     -f migrations/manual_patches/001_expand_catalog.sql
--
-- Covers two additions from the same change (open-world discovery via
-- Last.fm, see worker/tasks/expand_catalog.py):
--   1. listening_history.normalized_key — backfilled below; the expression
--      must stay identical to app/core/normalization.py's normalize_key(),
--      which is checked in tests/test_normalization.py against this exact
--      SQL through a live DB connection.
--   2. external_catalog — a brand-new table, so CREATE TABLE in init.sql
--      never touches an existing volume either; created fresh here.

ALTER TABLE listening_history ADD COLUMN IF NOT EXISTS normalized_key VARCHAR;

UPDATE listening_history
SET normalized_key =
    regexp_replace(lower(trim(artist_name)), '[^a-z0-9]', '', 'g')
    || '::' ||
    regexp_replace(lower(trim(track_name)), '[^a-z0-9]', '', 'g')
WHERE normalized_key IS NULL;

ALTER TABLE listening_history ALTER COLUMN normalized_key SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_listening_history_normalized_key
    ON listening_history (normalized_key);

CREATE TABLE IF NOT EXISTS external_catalog (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    artist_name         VARCHAR     NOT NULL,
    track_name          VARCHAR     NOT NULL,
    normalized_key      VARCHAR     NOT NULL UNIQUE,
    lastfm_url          VARCHAR,
    lastfm_match_score  FLOAT,
    seed_track_name     VARCHAR,
    global_play_count   INTEGER,
    tags                TEXT[],
    embedding           vector(384),
    first_seen_at       TIMESTAMP   NOT NULL DEFAULT clock_timestamp(),
    last_refreshed_at   TIMESTAMP   NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS idx_external_catalog_embedding_hnsw
    ON external_catalog USING hnsw (embedding vector_cosine_ops);
