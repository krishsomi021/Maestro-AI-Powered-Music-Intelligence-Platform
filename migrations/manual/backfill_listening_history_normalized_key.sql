-- One-time manual migration for an ALREADY-RUNNING database that has
-- listening_history rows predating the normalized_key column.
--
-- Not wired into docker-entrypoint-initdb.d — those scripts only execute
-- against an empty data volume, and this database already has data. Run
-- this once by hand, e.g.:
--
--   docker compose exec -T postgres psql -U <user> -d <db> \
--     -f migrations/manual/backfill_listening_history_normalized_key.sql
--
-- The expression below must produce identical output to app/core/normalization.py's
-- normalize_key() — this is checked in tests/test_normalization.py, which runs
-- both against the same inputs through the live DB connection.

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
