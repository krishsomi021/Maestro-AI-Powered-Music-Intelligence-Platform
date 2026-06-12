# Distributed Job Processing Platform

A backend infrastructure platform that processes long-running jobs asynchronously using **FastAPI**, **Celery**, **Redis**, and **PostgreSQL** — featuring a real fraud-detection model, a dual-strategy music recommender backed by **pgvector**, and a self-scheduling ETL pipeline.

Instead of blocking a web request while slow work runs, the platform accepts a job instantly, queues it, and processes it in the background. Clients receive a `job_id` immediately and poll for results. The same pattern used by Stripe for fraud detection, Instagram for content moderation, and Spotify for recommendation generation.

---

## Architecture

```
Client (Postman / curl / React dashboard)
        │
        ▼
  ┌─────────────┐     ┌───────────────┐     ┌──────────────────┐
  │   FastAPI   │────▶│     Redis     │────▶│  Celery Workers  │
  │  (Producer) │     │   (Broker)    │     │  (Consumers)     │
  └─────────────┘     └───────────────┘     └──────────────────┘
        │                     ▲                       │
        │              ┌──────────────┐               │
        │              │  Celery Beat │               │
        │              │ (Scheduler)  │               │
        │              └──────────────┘               │
        ▼                                             ▼
  ┌──────────────────────────────────────────────────────┐
  │                    PostgreSQL + pgvector             │
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

**Celery Beat** runs as a separate process and fires a scheduled ETL sync nightly — the platform produces work for itself, not just in response to API calls.

See [architecture.md](docs/architecture.md) for the full system design and data model.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| API | FastAPI + Pydantic v2 |
| Task Queue | Celery 5.4 |
| Scheduler | Celery Beat |
| Message Broker | Redis 7 |
| Database | PostgreSQL 16 + pgvector |
| Vector Embeddings | sentence-transformers (all-MiniLM-L6-v2, 384-dim) |
| External API | Spotify Web API (Client Credentials) |
| Frontend | React 19 + Vite + TypeScript |
| Containerization | Docker Compose (6 services) |
| Monitoring | Flower |
| Testing | pytest (48 tests) |
| CI/CD | GitHub Actions |

---

## Job Types

| Job | Type | Behavior |
|---|---|---|
| ML Inference | **Real** | **Tri-mode dispatch.** With `features` in the payload → pre-trained Random Forest fraud-detection model (14 features, StandardScaler pipeline). With `seed_tracks` + `recommender: "pgvector"` → content-based recommender using 384-dim sentence-transformer embeddings stored in Postgres. With `seed_tracks` (default) → item-based collaborative filtering. |
| ETL Pipeline | **Real** | Extracts Spotify streaming-history JSON from disk, filters and cleans plays, loads them into `listening_history` (idempotent via `INSERT ... ON CONFLICT DO NOTHING`), then runs an **enrichment phase**: resolves tracks against the Spotify API, fetches genres, generates embeddings, and stores them in `track_metadata`. |
| Report Generation | Mocked | Simulates PDF report generation (3–6s), returns report metadata. |

---

## The Two Recommenders

The platform ships **two independent recommendation strategies** that run side by side, selectable per request via `payload.recommender`:

**Collaborative filtering (`"cooccurrence"`, default)**
Item-based collaborative filtering trained offline on personal Spotify streaming history. Builds a co-occurrence matrix — tracks listened to together are similar. Inference is a dictionary lookup (near-instant). Serialized to `models/spotify_recommender.joblib`.

**Content-based (`"pgvector"`)**
Each enriched track is turned into a text representation — `"{artist} | {track} | {genres}"` — embedded with sentence-transformers (384 dimensions, `all-MiniLM-L6-v2`) and stored as a `vector(384)` column in Postgres. Recommendations are computed by taking the centroid of the seed embeddings and running a cosine-similarity query (`ORDER BY embedding <=> centroid`) directly in pgvector.

> **Why content-based instead of audio features?** The original design used Spotify's audio-features endpoint (danceability, energy, valence). Spotify deprecated that endpoint for all new apps in November 2024. This implementation pivots to genre + metadata text embeddings, which rely only on endpoints that remain available (search, artist metadata).

Keeping both models enables direct comparison — overlap, latency, and qualitative quality. See [recommender_evaluation.md](docs/recommender_evaluation.md).

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — must be running (allocate ≥ 6 GB memory; the worker image includes PyTorch via sentence-transformers)
- [Node.js](https://nodejs.org/) — for the frontend dev server
- [Git](https://git-scm.com/)
- [Postman](https://www.postman.com/) or `curl` for API testing
- A [Spotify Developer](https://developer.spotify.com/) app (Client ID + Secret) — only required for ETL enrichment and content-based recommendations

---

## Setup

**1. Clone the repository**
```bash
git clone https://github.com/krishsomi021/Distributed-Job-Processing-Platform.git
cd Distributed-Job-Processing-Platform
```

**2. Create your environment file**
```bash
cp .env_example .env
```

Open `.env` and set your values:
```bash
# PostgreSQL
POSTGRES_USER=your_username
POSTGRES_PASSWORD=your_password
POSTGRES_DB=jobsdb
POSTGRES_HOST=postgres    # use "localhost" when running outside Docker
POSTGRES_PORT=5432

# Celery / Redis
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# Spotify — required for ETL enrichment and the pgvector recommender
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret

# Celery Beat schedule (defaults to midnight UTC)
BEAT_ETL_HOUR=0
BEAT_ETL_MINUTE=0

# Optional
APP_ENV=development
LOG_LEVEL=info
RATE_LIMIT_ENABLED=true
CORS_ORIGINS=http://localhost:5173
```

**3. Add the ML model files**

Place your pre-trained fraud model at `models/fraud_model.joblib` (a scikit-learn pipeline with `StandardScaler` expecting 14 input features). For the collaborative-filtering recommender, place `models/spotify_recommender.joblib`.

> The `models/` and `data/` directories are gitignored — model and export files are never committed. The sentence-transformers embedding model (`all-MiniLM-L6-v2`) downloads automatically on first use.

**4. Start all services**
```bash
docker compose up --build
```

Wait for all six services to report healthy:
```
postgres | database system is ready to accept connections
redis    | Ready to accept connections tcp
fastapi  | Application startup complete.
worker   | celery@... ready.
beat     | beat: Starting...
flower   | Visit me at http://localhost:5555
```

The API is available at `http://localhost:8000`, and Flower monitoring at `http://localhost:5555`.

> **Schema changes:** the schema is owned exclusively by `migrations/init.sql`, which only runs when a fresh volume is created. Any schema change or Postgres image change requires: `docker compose down -v && docker compose up --build`.

---

## API Reference

### POST /jobs — Submit a job

Returns `202 Accepted` for a new job, `200 OK` if the idempotency key matches an existing job.

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

Optional: pass `"idempotency_key": "your-key"` in the request body to deduplicate submissions.

---

### GET /jobs/{id} — Poll job status

```bash
curl http://localhost:8000/jobs/67405415-002a-46e1-bf97-ef241fa50847
```

Response when complete (fraud detection):
```json
{
  "job_id": "67405415-002a-46e1-bf97-ef241fa50847",
  "job_type": "ml_inference",
  "status": "complete",
  "payload": {"features": [0.5, 1.2, "..."]},
  "result": {
    "prediction": 0,
    "prediction_label": "not_fraud",
    "model": "fraud_model"
  },
  "error": null,
  "created_at": "2026-06-01T22:30:13.462990",
  "started_at": "2026-06-01T22:30:13.530287",
  "completed_at": "2026-06-01T22:30:13.583091",
  "idempotency_key": null,
  "retry_count": 0
}
```

---

### GET /jobs — List recent jobs

```bash
curl "http://localhost:8000/jobs?limit=20"
```

Response:
```json
{
  "jobs": [{ "job_id": "...", "job_type": "ml_inference", "status": "complete", "..." : "..." }],
  "total": 1
}
```

---

### DELETE /jobs/{id} — Cancel a job

Cancels a `pending` or `running` job. Returns `409` if the job is already in a terminal state.

```bash
curl -X DELETE http://localhost:8000/jobs/67405415-002a-46e1-bf97-ef241fa50847
```

---

### GET /jobs/dead-letter — List permanently-failed jobs

```bash
curl "http://localhost:8000/jobs/dead-letter?limit=20"
```

---

### Example: Music recommendation (content-based pgvector)

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "ml_inference",
    "payload": {
      "seed_tracks": ["spotify:track:1j6kDJttn6wbVyMaM42Nxm"],
      "top_n": 5,
      "recommender": "pgvector"
    }
  }'
```

Result shape on completion:
```json
{
  "model": "pgvector_recommender",
  "top_n": 5,
  "seed_count": 1,
  "recommendations": [
    { "track_name": "Goldie", "artist_name": "A$AP Rocky", "uri": "spotify:track:...", "score": 0.7748 }
  ]
}
```

Omit `"recommender"` (or set it to `"cooccurrence"`) to use the collaborative-filtering model instead. Result shape is identical — only the `"model"` field changes to `"spotify_recommender"`.

---

### Example: ETL Pipeline job

Extracts Spotify streaming history from `StreamingHistory_music_*.json` files in `payload.source` (defaults to `data/spotify_export`), filters out short plays (< 30s / 30 000 ms) and invalid/podcast entries, loads cleaned plays into `listening_history`, then enriches new tracks via the Spotify API. Re-running is safe — duplicate plays are skipped via `UNIQUE(artist_name, track_name, end_time)`.

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "etl_pipeline",
    "payload": {
      "source": "data/spotify_export",
      "destination": "listening_history"
    }
  }'
```

Result shape on completion:
```json
{
  "status": "success",
  "pipeline": "spotify_etl",
  "source_files": ["StreamingHistory_music_0.json", "StreamingHistory_music_1.json"],
  "total_extracted": 16656,
  "skipped_short_plays": 9301,
  "skipped_invalid": 0,
  "new_records_loaded": 7355,
  "duplicate_records_skipped": 0,
  "tracks_enriched": 200,
  "enrichment_skipped": false
}
```

Enrichment is **non-fatal**: if Spotify credentials are missing or the API errors, the load still succeeds and `enrichment_skipped` is `true`. Enrichment is also **incremental**: it processes at most 200 unresolved tracks per run with a 1-second delay between Spotify search calls, so successive nightly syncs gradually enrich the full catalog without tripping the rate limiter.

---

### Track search

Backs the dashboard's seed-track autocomplete. Searches the `uri_to_meta` index embedded in `models/spotify_recommender.joblib`. Returns `[]` if the model file is not present.

```bash
curl "http://localhost:8000/tracks/search?q=arctic&limit=20"
```

Response:
```json
{
  "tracks": [
    { "uri": "spotify:track:...", "track_name": "Do I Wanna Know?", "artist_name": "Arctic Monkeys" }
  ]
}
```

---

### Listening history stats

Read-only endpoints over the `listening_history` table populated by the ETL pipeline.

```bash
curl "http://localhost:8000/stats/top-tracks?limit=10"
curl "http://localhost:8000/stats/top-artists?limit=10"
curl "http://localhost:8000/stats/listening-trends"
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

Result shape on completion:
```json
{
  "report_id": "3a4b5c6d-...",
  "pages": 12,
  "format": "pdf",
  "download_url": "mocked://reports/3a4b5c6d-....pdf"
}
```

---

### Rate limiting

`POST /jobs` and `DELETE /jobs/{id}` are rate limited to **100 requests per minute per client IP**. Exceeding the limit returns `429 Too Many Requests` with a `Retry-After` header. GET endpoints are not rate limited — reads are cheap and polling is the intended client pattern.

Rate-limit counters are stored in Redis (the same instance used as the Celery broker, via `CELERY_BROKER_URL`), so limits are enforced consistently across multiple FastAPI replicas. Disable with `RATE_LIMIT_ENABLED=false`.

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

## Scheduled Jobs (Celery Beat)

Celery Beat runs as a dedicated service (`restart: unless-stopped`) and fires `trigger_etl_sync` on a schedule (default midnight UTC, configurable via `BEAT_ETL_HOUR` / `BEAT_ETL_MINUTE`). The task creates and enqueues an ETL job exactly as the API would — it is a producer, not a job processor — guarded by an idempotency key (`scheduled_etl_daily`) so overlapping triggers never create duplicate runs.

Trigger it manually without waiting for the schedule:
```bash
docker compose exec worker celery -A worker.celery_app call worker.tasks.scheduled.trigger_etl_sync
```

---

## Frontend Dashboard

A React + Vite + TypeScript single-page dashboard in `frontend/` with four pages:

| Page | Route | What it does |
|---|---|---|
| **Job Monitoring** | `/` | Submit jobs, watch live status, cancel pending/running jobs, view dead-letter queue |
| **Queue Statistics** | `/queue-stats` | Status and job-type breakdowns (recharts pie + bar), Flower link |
| **Recommendations** | `/recommendations` | Seed-track autocomplete, recommender mode toggle, listening analytics (top tracks/artists/trends) |
| **Job History** | `/history` | Filterable browse table by status and job type |

```bash
cd frontend
cp .env.example .env.local   # set VITE_API_BASE_URL if the API isn't on localhost:8000
npm install
npm run dev                  # served at http://localhost:5173
```

Requires `docker compose up` to be running first. CORS is configured via `CORS_ORIGINS` in `.env` (default: `http://localhost:5173`).

---

## Project Structure

```
distributed-job-processing-platform/
├── app/
│   ├── main.py                    # FastAPI app, routers, CORS middleware, rate-limit handler
│   ├── config.py                  # pydantic-settings: DB, Redis, Spotify, Beat schedule, CORS
│   ├── api/
│   │   ├── routes/
│   │   │   ├── jobs.py            # POST/GET/DELETE /jobs, GET /jobs/dead-letter
│   │   │   ├── stats.py           # GET /stats/top-tracks, /top-artists, /listening-trends
│   │   │   └── tracks.py          # GET /tracks/search (dashboard seed-track autocomplete)
│   │   ├── services/
│   │   │   ├── job_service.py     # submit, enqueue, cancel business logic
│   │   │   └── track_search_service.py  # substring search over recommender model metadata
│   │   └── dao/
│   │       ├── job_dao.py         # Job DB operations
│   │       └── stats_dao.py       # Aggregate queries over listening_history
│   ├── db/
│   │   └── async_session.py       # Async SQLAlchemy engine + asyncpg (FastAPI)
│   ├── models/
│   │   ├── job.py                 # Job ORM model
│   │   ├── listening_history.py   # Spotify play history ORM model
│   │   └── track_metadata.py      # Enriched track metadata + vector(384) embedding
│   ├── schemas/
│   │   ├── job.py                 # Job request/response Pydantic schemas
│   │   ├── stats.py               # Stats response schemas
│   │   └── track.py               # Track search response schema
│   └── core/
│       ├── enums.py               # JobType and JobStatus enums
│       └── rate_limit.py          # slowapi Limiter (Redis-backed, 100 req/min)
│
├── worker/
│   ├── celery_app.py              # Celery instance, broker config, beat_schedule
│   ├── clients/
│   │   └── spotify.py             # Spotify Client Credentials client (token cache, 429 handling)
│   ├── db/
│   │   └── sync_session.py        # Sync SQLAlchemy engine + psycopg2; registers pgvector type
│   ├── ml/
│   │   └── embeddings.py          # sentence-transformers wrapper (all-MiniLM-L6-v2, 384-dim)
│   └── tasks/
│       ├── base.py                # BaseJobTask — retry / dead-letter behavior
│       ├── ml_inference.py        # Tri-mode: fraud detection + co-occurrence + pgvector recommenders
│       ├── etl_pipeline.py        # Extract → transform → load → Spotify enrichment phase
│       ├── report_generation.py   # Mocked report task
│       └── scheduled.py           # trigger_etl_sync (Celery Beat meta-orchestrator)
│
├── frontend/                      # React 19 + Vite + TypeScript dashboard (4 pages)
├── models/                        # Model files — gitignored (fraud_model.joblib, spotify_recommender.joblib)
├── data/                          # Spotify export JSON files — gitignored
├── migrations/
│   └── init.sql                   # vector extension + jobs + listening_history + track_metadata
├── scripts/
│   ├── train_recommender.py       # Trains the co-occurrence recommender
│   ├── verify_spotify_access.py   # Verifies Spotify credentials and available endpoints
│   └── test_cancel.py             # Standalone manual cancellation test (not a pytest test)
├── tests/                         # 48 pytest tests (see Running Tests)
├── docs/
│   ├── architecture.md            # Full system design, data model, job lifecycle
│   └── recommender_evaluation.md  # Collaborative vs content-based comparison
│
├── .github/workflows/ci.yml       # GitHub Actions CI
├── .env_example                   # Template for .env
├── docker-compose.yml             # 6 services: postgres, redis, fastapi, worker, beat, flower
├── Dockerfile                     # Shared Python 3.12-slim image
└── requirements.txt               # All Python dependencies pinned
```

---

## Running Tests

```bash
docker compose exec fastapi pytest tests/ -v
```

**48 tests across 7 files:**

| File | Count | What it covers |
|---|---|---|
| `test_jobs_api.py` | 14 | Submission, polling, idempotency, cancellation, dead-letter, rate limiting |
| `test_tasks.py` | 9 | Fraud inference, both recommenders, ETL, report generation, retry/dead-letter |
| `test_enrichment.py` | 7 | Skip-already-resolved, non-fatal failure modes, batch cap, search delay |
| `test_etl_pipeline.py` | 5 | ETL filters, idempotency (delta-based assertions), stats endpoint queries |
| `test_pgvector_recommender.py` | 5 | Ranked results shape, missing seeds, payload routing, unparseable URIs |
| `test_tracks.py` | 5 | Track search by name/artist, empty query, missing model graceful handling |
| `test_scheduled.py` | 3 | Job creation, dedup-skip on active job, re-creation after terminal job |

```
48 passed
```

Test fixtures use an external-transaction + SAVEPOINT rollback pattern (SQLAlchemy `join_transaction_mode="create_savepoint"`) — no `create_all`/`drop_all` anywhere, all tables survive every test run intact.

CI runs the full suite automatically on every push via GitHub Actions.

---

## Key Design Decisions

**`task_acks_late = True`** — Celery's default acknowledges a task on receipt; a worker crash mid-execution permanently loses the job. This setting delays acknowledgment until after completion, so a crashed worker returns the task to the queue automatically. Combined with idempotency keys, the system is both reliable and safe against duplicate execution.

**Two SQLAlchemy session setups** — FastAPI uses async SQLAlchemy + asyncpg (non-blocking, required for the async event loop). Celery workers use sync SQLAlchemy + psycopg2 (no event loop in worker processes). Sessions are never shared between layers. The sync session also registers pgvector's custom type on connect so embeddings deserialize as float arrays.

**Schema owned by `init.sql`** — No `create_all`/`drop_all` anywhere in application code or test fixtures. A single SQL migration file is the explicit, auditable source of truth for all three tables, avoiding cross-table destruction when multiple ORM models share a declarative `Base`.

**Three recommenders in one task** — `run_ml_inference` dispatches across fraud detection, co-occurrence collaborative filtering, and content-based pgvector similarity based on the payload shape. All three share the same job lifecycle, retry behavior, and API surface.

**Scheduler as a producer** — `trigger_etl_sync` is a plain Celery task, not a `BaseJobTask`. It creates and enqueues jobs; it doesn't process them. Coupling it to the job-processor base class would attach retry/dead-letter behavior it doesn't need — if Beat fires the task and it fails, Beat simply tries again at the next scheduled interval.

**Postgres as system of record, not Redis** — Redis holds tasks in-flight; Postgres holds the durable history of every job and all embeddings. Client reads always go to Postgres, never the queue.

**Non-fatal enrichment** — The Spotify enrichment phase never fails the ETL load. External API outages, missing credentials, or individual track-resolution failures degrade gracefully (those tracks stay unenriched) rather than failing the whole pipeline.

**Two recommenders kept side by side** — Rather than replacing collaborative filtering with the vector model, both run behind a payload flag. This provides a baseline for evaluating the new model — the difference between "I built it" and "I evaluated it."

---

## Roadmap

**v1 — Reliability** ✅
- Exponential backoff retry, dead-letter queue, idempotency keys, Flower monitoring, job cancellation with race-condition fix

**v2 — Scale & Scheduling** ✅
- Celery Beat scheduled ETL sync
- React + Vite dashboard with live job status, queue stats, recommendations, and job history

**ML / Recommender** ✅
- Co-occurrence collaborative filter trained on personal listening history
- Content-based recommender via genre/metadata embeddings + pgvector cosine similarity
- Self-enriching ETL pipeline against the Spotify API

**Future**
- Named queues per job type with per-queue concurrency tuning
- Hybrid recommender blending collaborative and content-based signals
- Spotify OAuth for live listening-history sync (replacing manual JSON export)
- AWS deployment (EC2 + RDS + ElastiCache)
