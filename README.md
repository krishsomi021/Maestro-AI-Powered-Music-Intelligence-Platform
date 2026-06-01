# Distributed Job Processing Platform

A backend infrastructure platform that processes long-running jobs asynchronously using FastAPI, Celery, Redis, and PostgreSQL.

## What it does

Decouples slow task execution (ML inference, ETL pipelines, report generation) from the request loop using a producer–broker–consumer pattern. Clients submit a job and get a `job_id` back immediately; a Celery worker picks up the task, runs it, and writes the result to Postgres. Clients poll for status.

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Pydantic v2 |
| Task queue | Celery 5 (`task_acks_late=True`) |
| Broker | Redis 7 |
| Database | PostgreSQL 16 (async SQLAlchemy for API, sync for workers) |
| Containers | Docker Compose |

---

## Setup

### 1. Clone and configure

```bash
git clone https://github.com/krishsomi021/Distributed-Job-Processing-Platform.git
cd Distributed-Job-Processing-Platform
cp .env.example .env
# Edit .env if you want different credentials
```

### 2. Train the recommendation model (required before first ml_inference job)

Place your Spotify data export in `data/spotify_export/` (request it via Spotify → Settings → Privacy → Download your data). Then run:

```bash
python scripts/train_recommender.py ./data/spotify_export
# Produces models/spotify_recommender.joblib
```

The script handles both the basic (`StreamingHistory*.json`) and extended (`Streaming_History_Audio_*.json`) Spotify export formats automatically.

### 3. Start all services

```bash
docker compose up --build
```

This starts FastAPI on `localhost:8000`, a Celery worker, Redis, and Postgres. The `jobs` table is created automatically from `migrations/init.sql`.

---

## API

### Submit a job

```bash
# ML inference — Spotify recommendations
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_type": "ml_inference", "payload": {"seed_tracks": ["a1b2c3d4e5f6a7b8", "b2c3d4e5f6a7b8c9"], "top_n": 10}}'

# ETL pipeline
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_type": "etl_pipeline", "payload": {"source": "raw_transactions", "destination": "clean_transactions"}}'

# Report generation
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_type": "report_generation", "payload": {"report_type": "monthly_summary", "date_range": {"start": "2026-01-01", "end": "2026-01-31"}}}'
```

Response (202):
```json
{"job_id": "uuid", "status": "pending", "created_at": "2026-06-01T12:00:00"}
```

### Poll job status

```bash
curl http://localhost:8000/jobs/{job_id}
```

ml_inference result shape:
```json
{
  "recommendations": [
    {"track_id": "a1b2c3d4e5f6a7b8", "track_name": "Song Title", "artist": "Artist Name", "score": 0.94}
  ],
  "seed_count": 2,
  "model": "spotify_recommender"
}
```

### List recent jobs

```bash
curl http://localhost:8000/jobs
curl http://localhost:8000/jobs?limit=50
```

---

## Running tests

```bash
docker compose exec fastapi pytest tests/
```

Or locally (requires Postgres + Redis running):

```bash
pytest tests/
```

---

## Job status lifecycle

```
pending → running → complete
                 → failed
```

Workers own all status transitions after the initial `pending` write by the API.

---

## Getting track IDs

Track IDs are deterministic hashes built from `artist + track_name` by the training script. To find the ID for a specific track, run the training script and inspect `models/spotify_recommender.joblib`:

```python
import joblib
model = joblib.load("models/spotify_recommender.joblib")
# model["track_lookup"] maps track_id → {"track_name": ..., "artist": ...}
# Reverse lookup: find track_id for a known track
lookup = {v["track_name"]: k for k, v in model["track_lookup"].items()}
print(lookup.get("Song Title"))
```
