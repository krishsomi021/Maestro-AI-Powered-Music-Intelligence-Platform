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
- `worker/tasks/ml_inference.py` — run_ml_inference task (real, uses joblib model)
- `worker/tasks/etl_pipeline.py` — run_etl_pipeline task (mocked, 5–8s sleep)
- `worker/tasks/report_generation.py` — run_report_generation task (mocked, 3–6s sleep)
- `tests/conftest.py` — pytest fixtures (test client, DB session override)
- `tests/test_jobs_api.py` — API endpoint tests
- `tests/test_tasks.py` — worker task unit tests
- `.github/workflows/ci.yml` — GitHub Actions CI running pytest on push to main

### Next
- Day 2: Wire services end-to-end, verify `docker compose up --build` runs cleanly, smoke-test all 3 endpoints
