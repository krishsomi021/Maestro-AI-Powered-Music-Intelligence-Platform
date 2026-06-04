# Project Status

## Day 1 — Project Foundation ✅

**Goal:** Repo scaffolded, Docker runs, services talk to each other.

### Completed
- Full folder structure matching project_spec.md
- `docker-compose.yml` with 4 services: fastapi, worker, redis, postgres
- `Dockerfile` (single image for both fastapi and worker)
- `migrations/init.sql` — jobs table with all columns from data model
- `requirements.txt` — all dependencies pinned (Python 3.12)
- `.env.example` — all required env vars with placeholder values
- `.env` — local values (gitignored)
- `app/config.py` — pydantic-settings loading all env vars; exposes `async_database_url` and `sync_database_url` properties
- `app/core/enums.py` — `JobType` and `JobStatus` StrEnums
- `app/models/job.py` — SQLAlchemy ORM model for jobs table
- `app/schemas/job.py` — Pydantic v2 request/response schemas
- `app/db/async_session.py` — async SQLAlchemy engine + session for FastAPI
- `app/api/routes/jobs.py` — POST /jobs, GET /jobs/{id}, GET /jobs
- `app/main.py` — FastAPI app with router registration
- `worker/celery_app.py` — Celery instance, `task_acks_late=True`
- `worker/db/sync_session.py` — sync SQLAlchemy engine + session for workers
- `worker/tasks/ml_inference.py` — run_ml_inference task (Spotify recommender, uses joblib model)
- `worker/tasks/etl_pipeline.py` — run_etl_pipeline task (mocked, 5–8s sleep)
- `worker/tasks/report_generation.py` — run_report_generation task (mocked, 3–6s sleep)
- `tests/conftest.py` — pytest fixtures (test client, DB session override)
- `tests/test_jobs_api.py` — API endpoint tests
- `tests/test_tasks.py` — worker task unit tests
- `.github/workflows/ci.yml` — GitHub Actions CI running pytest on push to main

---

## v1 Reliability Features ✅

**Goal:** Exponential backoff retries, dead-letter queue, idempotency, job cancellation, Flower monitoring.

### Completed
- `worker/tasks/base.py` — `BaseJobTask` with `on_retry` (sets `retrying` status, increments `retry_count`) and `on_failure` (marks `failed`, routes to dead_letter queue); `handle_dead_letter` shared task
- All three worker tasks updated to inherit `BaseJobTask` and use `self.retry(exc, countdown=2^n + jitter)`
- `app/core/enums.py` — added `RETRYING` and `CANCELLED` statuses
- `app/models/job.py` — added `idempotency_key`, `retry_count`, `max_retries` columns
- `migrations/init.sql` — updated with new columns and `idx_jobs_idempotency_key` partial index
- `app/schemas/job.py` — updated request/response schemas for new fields; added `CancelJobResponse`, `JobListResponse`
- `app/api/dao/job_dao.py` — DAO layer: `get_by_id`, `get_by_idempotency_key`, `get_dead_letter_jobs`, `set_cancelled`
- `app/api/services/job_service.py` — service layer: `find_by_idempotency_key`, `get_dead_letter_jobs`, `cancel_job` (with Celery revoke)
- `app/api/routes/jobs.py` — full rewrite: idempotency on POST, DELETE /{id}, GET /dead-letter (static route before /{job_id}); `_enqueue_task` sets `task_id=job_id`
- `worker/celery_app.py` — added `worker.tasks.base` to include list, `task_default_queue=celery`
- `docker-compose.yml` — added `flower` service (port 5555); worker command updated to `-Q celery,dead_letter`
- `requirements.txt` — added `flower==2.0.1`
- Tests — added 10 new tests covering idempotency, cancellation, dead-letter, retry hooks, and cancelled-job guard

### Test count
- 9 original API tests (all passing)
- 4 original task unit tests
- 10 new v1 tests (idempotency, cancellation, dead-letter, retry, on_failure guards)
- **Total: 23 tests**

---

## Next
- v2: Named per-type queues, WebSocket push for job status, execution log table, frontend dashboard
