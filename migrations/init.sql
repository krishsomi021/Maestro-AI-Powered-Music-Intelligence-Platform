CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS track_metadata (
    id                UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    spotify_track_id  VARCHAR,
    artist_name       VARCHAR     NOT NULL,
    track_name        VARCHAR     NOT NULL,
    spotify_artist_id VARCHAR,
    genres            TEXT[],
    popularity        INTEGER,
    embedding         vector(384),
    enriched_at       TIMESTAMP   NOT NULL DEFAULT NOW(),
    UNIQUE(artist_name, track_name)
);

CREATE INDEX IF NOT EXISTS idx_track_metadata_spotify_track_id
    ON track_metadata (spotify_track_id)
    WHERE spotify_track_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS jobs (
    id              UUID        PRIMARY KEY,
    job_type        VARCHAR     NOT NULL,
    status          VARCHAR     NOT NULL DEFAULT 'pending',
    payload         JSONB       NOT NULL,
    result          JSONB,
    error           TEXT,
    created_at      TIMESTAMP   NOT NULL,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    idempotency_key VARCHAR,
    retry_count     INTEGER     NOT NULL DEFAULT 0,
    max_retries     INTEGER     NOT NULL DEFAULT 3
);

CREATE INDEX IF NOT EXISTS idx_jobs_idempotency_key ON jobs (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS listening_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artist_name VARCHAR NOT NULL,
    track_name VARCHAR NOT NULL,
    end_time TIMESTAMP NOT NULL,
    ms_played INTEGER NOT NULL,
    -- Canonical artist::track dedup key, shared with external_catalog.normalized_key
    -- (see app/core/normalization.py). Populated on fresh installs directly; an
    -- already-running database needs the one-time
    -- migrations/manual_patches/001_expand_catalog.sql instead,
    -- since docker-entrypoint-initdb.d scripts only run against an empty volume.
    normalized_key VARCHAR NOT NULL,
    imported_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(artist_name, track_name, end_time)
);

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

-- HNSW over ivfflat: ivfflat needs existing data to train its cluster centroids,
-- but this table starts empty and grows via periodic expand_catalog runs. HNSW
-- builds incrementally with no training step; the tradeoff (slower inserts) is
-- fine since writes only happen in the offline Celery task, never the query path.
CREATE INDEX IF NOT EXISTS idx_external_catalog_embedding_hnsw
    ON external_catalog USING hnsw (embedding vector_cosine_ops);
