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
