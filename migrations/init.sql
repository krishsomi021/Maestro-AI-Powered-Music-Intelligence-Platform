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
    imported_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(artist_name, track_name, end_time)
);
