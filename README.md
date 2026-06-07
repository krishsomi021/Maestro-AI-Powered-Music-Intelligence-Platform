# Distributed Job Processing Platform

A backend infrastructure platform that processes long-running jobs asynchronously using **FastAPI**, **Celery**, **Redis**, and **PostgreSQL**.

Instead of blocking a web request while slow work runs, the platform accepts a job instantly, queues it, and processes it in the background. Clients receive a `job_id` immediately and poll for results. The same pattern used by Stripe for fraud detection, Instagram for content moderation, and Spotify for recommendation generation.

---

## Architecture

```
Client (Postman / curl)
        │
        ▼
  ┌─────────────┐     ┌───────────────┐     ┌──────────────────┐
  │   FastAPI   │────▶│     Redis     │────▶│  Celery Workers  │
  │  (Producer) │     │   (Broker)    │     │  (Consumers x8)  │
  └─────────────┘     └───────────────┘     └──────────────────┘
        │                                            │
        │                                            │
        ▼                                            ▼
  ┌──────────────────────────────────────────────────────┐
  │                    PostgreSQL                        │
  │               (System of Record)                     │
  └──────────────────────────────────────────────────────┘
```

**Request flow:**
1. Client sends `POST /jobs` → FastAPI validates payload
2. FastAPI writes job row to Postgres with `status=pending`
3. FastAPI enqueues Celery task to Redis → returns `job_id` instantly
4. Worker pulls task from Redis → sets `status=running`
5. Worker executes job logic → writes result → sets `status=complete`
6. Client polls `GET /jobs/{id}` to retrieve result

See [architecture.md](architecture.md) for the full system design and data model.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| API | FastAPI + Pydantic v2 |
| Task Queue | Celery 5.4 |
| Message Broker | Redis 7 |
| Database | PostgreSQL 16 + SQLAlchemy |
| Containerization | Docker Compose |
| Testing | PyTest |
| CI/CD | GitHub Actions |

---

## Job Types

| Job | Type | Behavior |
|---|---|---|
| ML Inference | **Real** | Loads a pre-trained Random Forest fraud detection model, runs `.predict()` on 14 input features, returns fraud/not-fraud prediction in ~53ms |
| ETL Pipeline | Mocked | Simulates a data transform pipeline (5–8s), returns rows processed and duration |
| Report Generation | Mocked | Simulates PDF report generation (3–6s), returns report metadata |

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — must be running
- [Git](https://git-scm.com/)
- [Postman](https://www.postman.com/) or `curl` for API testing

---

## Setup

**1. Clone the repository**
```bash
git clone https://github.com/krishsomi021/Distributed-Job-Processing-Platform.git
cd Distributed-Job-Processing-Platform
```

**2. Create your environment file**
```bash
cp .env.example .env
```

Open `.env` and set your values:
```bash
POSTGRES_USER=your_username
POSTGRES_PASSWORD=your_password
POSTGRES_DB=jobsdb
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

APP_ENV=development
LOG_LEVEL=info
```

**3. Add the ML model file**

Place your pre-trained model at `models/fraud_model.joblib`. The model must be a scikit-learn pipeline with a `StandardScaler` and expect 14 input features.

> The `models/` directory is gitignored — model files are never committed to the repository.

**4. Start all services**
```bash
docker compose up --build
```

Wait for all four containers to report healthy:
```
postgres | database system is ready to accept connections
redis    | Ready to accept connections tcp
fastapi  | Application startup complete.
worker   | celery@... ready.
```

The API is now available at `http://localhost:8000`.

---

## API Reference

### POST /jobs — Submit a job

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "ml_inference",
    "payload": {
      "features": [0.5, 1.2, 3.4, 0.0, 2.1, 0.8, 1.1, 0.3, 2.7, 0.6, 1.4, 0.9, 3.1, 0.2]
    }
  }'
```

Response `202 Accepted`:
```json
{
  "job_id": "67405415-002a-46e1-bf97-ef241fa50847",
  "status": "pending",
  "created_at": "2026-06-01T22:30:13.462990"
}
```

---

### GET /jobs/{id} — Poll job status

```bash
curl http://localhost:8000/jobs/67405415-002a-46e1-bf97-ef241fa50847
```

Response when complete:
```json
{
  "job_id": "67405415-002a-46e1-bf97-ef241fa50847",
  "job_type": "ml_inference",
  "status": "complete",
  "payload": { "features": [0.5, 1.2, 3.4, 0.0, 2.1, 0.8, 1.1, 0.3, 2.7, 0.6, 1.4, 0.9, 3.1, 0.2] },
  "result": {
    "prediction": 0,
    "prediction_label": "not_fraud",
    "model": "fraud_model"
  },
  "error": null,
  "created_at": "2026-06-01T22:30:13.462990",
  "started_at": "2026-06-01T22:30:13.530287",
  "completed_at": "2026-06-01T22:30:13.583091"
}
```

---

### GET /jobs — List recent jobs

```bash
curl "http://localhost:8000/jobs?limit=20"
```

---

### Example: ETL Pipeline job

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "etl_pipeline",
    "payload": {
      "source": "raw_transactions",
      "destination": "clean_transactions"
    }
  }'
```

---

### Example: Report Generation job

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "report_generation",
    "payload": {
      "report_type": "monthly_summary",
      "date_range": { "start": "2026-01-01", "end": "2026-01-31" }
    }
  }'
```

---

### Rate limiting

`POST /jobs` and `DELETE /jobs/{id}` are rate limited to **100 requests per minute per client IP**. Exceeding the limit returns `429 Too Many Requests` with a `Retry-After` header.

GET endpoints are not rate limited — reads are cheap and polling is the intended client pattern.

Rate limit counters are stored in Redis (the same instance used as the Celery broker), so the limit is enforced consistently across multiple FastAPI replicas.

---

### Response codes

| Code | Meaning |
|---|---|
| `202 Accepted` | New job submitted successfully |
| `200 OK` | Duplicate job (idempotency key match) |
| `404 Not Found` | Job ID does not exist |
| `409 Conflict` | Cannot cancel a job in a terminal state |
| `422 Unprocessable Entity` | Invalid request body |
| `429 Too Many Requests` | Rate limit exceeded on POST or DELETE |

---

### Job status values

| Status | Meaning |
|---|---|
| `pending` | Job submitted, waiting for a worker |
| `running` | Worker is executing the job |
| `retrying` | Worker encountered an error and is retrying with backoff |
| `complete` | Job finished successfully, result is available |
| `failed` | Job exceeded max retries, error message is available |
| `cancelled` | Job was cancelled before or during execution |

---

## Project Structure

```
job-processing-platform/
├── app/
│   ├── main.py                    # FastAPI app instantiation
│   ├── config.py                  # Settings loaded from environment
│   ├── api/
│   │   └── routes/
│   │       └── jobs.py            # POST /jobs, GET /jobs/{id}, GET /jobs
│   │   └── services/
│   │       └── job_service.py     # Business logic — submit, enqueue
│   │   └── dao/
│   │       └── job_dao.py         # Database operations — create, get, list
│   ├── db/
│   │   └── async_session.py       # Async SQLAlchemy engine (FastAPI)
│   ├── models/
│   │   └── job.py                 # SQLAlchemy ORM model
│   ├── schemas/
│   │   └── job.py                 # Pydantic request/response schemas
│   └── core/
│       ├── enums.py               # JobType and JobStatus enums
│       └── rate_limit.py          # slowapi Limiter (Redis-backed, 100 req/min)
│
├── worker/
│   ├── celery_app.py              # Celery instance, broker config
│   ├── db/
│   │   └── sync_session.py        # Sync SQLAlchemy engine (Celery)
│   └── tasks/
│       ├── ml_inference.py        # Real ML inference task
│       ├── etl_pipeline.py        # Mocked ETL task
│       └── report_generation.py   # Mocked report task
│
├── models/                        # Model files — gitignored
├── migrations/
│   └── init.sql                   # Jobs table schema
├── tests/
│   ├── conftest.py                # Fixtures
│   ├── test_jobs_api.py           # 7 API tests
│   └── test_tasks.py              # 2 worker unit tests
│
├── .github/workflows/ci.yml       # GitHub Actions CI
├── docker-compose.yml             # 4 services: fastapi, worker, redis, postgres
├── Dockerfile                     # Single image for fastapi + worker
└── requirements.txt
```

---

## Running Tests

```bash
docker compose exec fastapi pytest tests/ -v
```

Expected output:
```
tests/test_jobs_api.py::test_submit_ml_inference_job       PASSED
tests/test_jobs_api.py::test_submit_etl_pipeline_job       PASSED
tests/test_jobs_api.py::test_submit_report_generation_job  PASSED
tests/test_jobs_api.py::test_submit_invalid_job_type       PASSED
tests/test_jobs_api.py::test_get_job                       PASSED
tests/test_jobs_api.py::test_get_job_not_found             PASSED
tests/test_jobs_api.py::test_list_jobs                     PASSED
tests/test_tasks.py::test_etl_task                         PASSED
tests/test_tasks.py::test_report_task                      PASSED

9 passed
```

CI runs the full test suite automatically on every push to main via GitHub Actions.

---

## Key Design Decisions

**`task_acks_late = True`** — Celery's default acknowledges a task on receipt. If a worker crashes mid-execution the job is permanently lost. This setting delays acknowledgment until after completion, so a crashed worker returns the task to the queue automatically.

**Two SQLAlchemy session setups** — FastAPI uses async SQLAlchemy + asyncpg (non-blocking, required for the async event loop). Celery workers use sync SQLAlchemy + psycopg2 (no event loop in worker processes). Sessions are never shared between layers.

**Postgres as system of record, not Redis** — Redis holds tasks in-flight. Postgres holds the durable history of every job. All client reads go to Postgres — never to the queue directly.

**UUID job IDs generated by the application** — Job identity is decoupled from database insertion order. The client receives a `job_id` before the database write completes.

---

## Roadmap

**v1 — Reliability**
- Exponential backoff retry logic
- Dead letter queue for permanently failed jobs
- Idempotency keys on job submission
- Flower monitoring UI
- Job cancellation endpoint

**v2 — Observability & Scale**
- React dashboard with live job status
- Named queues per job type (ml-queue, etl-queue, report-queue)
- Job priority levels
- Celery Beat for scheduled jobs

**Spotify Recommender (in progress)**
- Swap ML inference task to a personalized music recommendation engine trained on personal Spotify listening history
- Spotify Web API + OAuth integration for live data
- React UI with listening analytics dashboard and recommendations page

**AWS Deployment**
- EC2 + RDS (PostgreSQL) + ElastiCache (Redis)
- Same Docker Compose, environment-swapped connection strings
