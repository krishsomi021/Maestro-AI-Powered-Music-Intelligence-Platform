CREATE TABLE IF NOT EXISTS jobs (
    id          UUID        PRIMARY KEY,
    job_type    VARCHAR     NOT NULL,
    status      VARCHAR     NOT NULL DEFAULT 'pending',
    payload     JSONB       NOT NULL,
    result      JSONB,
    error       TEXT,
    created_at  TIMESTAMP   NOT NULL,
    started_at  TIMESTAMP,
    completed_at TIMESTAMP
);
