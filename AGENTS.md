# AGENTS.md

> Persistent context for Codex. Read this first, every session.
>
> **Note:** This file supersedes the existing `AGENTS.md` on `dev` by adding the agent
> layer. Before overwriting, diff against the current `dev` version and preserve any
> invariants not reproduced here — do not blindly replace.

---

## What This Project Is

A backend platform with multiple capability layers built on a shared FastAPI + Celery +
PostgreSQL + Redis stack:

1. **Job processing** — Celery workers, retries, dead-letter queue, idempotency, cancellation
2. **ML inference** — Random Forest fraud model + dual music recommender (co-occurrence + pgvector)
3. **ETL** — Spotify listening-history load + metadata enrichment
4. **LLM analyst agent** *(this branch)* — natural-language analyst over the listening data

The agent is a **capability layer on top of the existing platform**, not a separate system.
It reuses the existing PostgreSQL (incl. pgvector), Redis, FastAPI app, and Docker Compose stack.

---

## Current Work: `feat/agent-layer`

Building the LLM Spotify Analyst Agent. Branch off `dev`, merge back to `dev` when done.
See `plan.md` for phases, `tasks.md` for the working checklist, `ARCHITECTURE.md` for design,
`PRD.md` for the why.

**This is documentation-and-feature work on a portfolio project targeting backend SWE roles.**
Architecture quality that survives technical-interview scrutiny matters more than feature count.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Pydantic v2 |
| Task queue | Celery 5.4 + Redis 7 (broker) |
| Database | PostgreSQL 16 + pgvector (`pgvector/pgvector:pg16`) |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` (384-dim) |
| LLM | Anthropic `Codex-haiku-4-5` (default) / `Codex-sonnet-4-6` (quality + eval judge) |
| Frontend | React 19 + Vite + TypeScript, Recharts |
| Containers | Docker Compose (project name: `jobplatform`) |
| CI | GitHub Actions |
| Testing | pytest |

**Async/sync split:** FastAPI side uses async SQLAlchemy / asyncpg. Celery side uses sync
SQLAlchemy / psycopg2. The agent runs on the **FastAPI (async)** side.

---

## Hard Invariants (do not violate)

- **Schema is owned exclusively by SQL migrations.** No `create_all` / `drop_all` anywhere
  in application code. New agent tables go in `migrations/002_agent_schema.sql`.
- **`task_acks_late=True`** on Celery — non-negotiable.
- **All config via pydantic-settings.** No hardcoded values, no secrets in source or in
  schema files. New env vars are added as typed settings fields.
- **`name: jobplatform` is the first line of `docker-compose.yml`.**
- **`BaseJobTask` is for job processors only.** Meta-orchestrators (e.g. `trigger_etl_sync`)
  are plain `@app.task`.
- **`migrations/init.sql` only runs on a fresh volume.** Schema changes require
  `docker compose down -v`. The agent migration follows the same rule.
- **CPU-only torch** in the Dockerfile (installed first from the PyTorch CPU index).
- **The read-only DB role is NOT created inside the schema migration.** It is provisioned by
  a separate idempotent script (`scripts/provision_agent_role.py`) that reads the password
  from pydantic-settings. Schema files contain no interpolated secrets.

---

## Agent-Specific Conventions

- **One streaming mode.** The agent loop streams every LLM iteration via the Anthropic
  streaming API. It forwards text deltas to the client and accumulates `tool_use` blocks
  silently. **Never** generate a response with a non-streaming call and then re-generate it
  to stream — the final answer must be produced exactly once.
- **One agent endpoint for a turn.** `POST /agent/message` returns the SSE stream directly
  via `StreamingResponse`. There is no separate GET stream endpoint and no `EventSource` on
  the client — the frontend consumes the POST response with `fetch()` + a `ReadableStream`
  reader. This avoids the Redis hand-off race and EventSource's replay-on-reconnect.
- **SQL safety is layered, role-first.** Authoritative boundary = the `agent_readonly`
  PostgreSQL role (SELECT only, on `listening_history` + `track_metadata`). `sqlparse`
  provides a cheap pre-filter (single statement, SELECT-only, no DDL/DML keywords). **Do not
  add naive substring table-name matching** — it produces false positives (e.g. a track
  named "Steve Jobs") and is redundant with the role grants.
- **Charts are a property of tool results, not a tool.** Tools that produce chartable data
  attach a server-built `chart_spec` to their `ToolResult`. There is no `generate_chart`
  tool — the LLM never transcribes data arrays into tool input.
- **Memory: Redis is the hot working set, Postgres is durable history.** Redis holds the
  live conversation (24h TTL) for the loop; `agent_messages` is the durable copy and **must
  be read** by `GET /agent/sessions/{id}/messages` (it is not write-only).
- **Eval judge model is held constant.** LLM-as-judge always uses `Codex-sonnet-4-6`
  regardless of which model generated the answer — never let a model judge its own output.
- **Time-of-day aggregates use local time.** `listening_history.ts` is UTC; convert with
  `ts AT TIME ZONE '<local_tz>'` before `EXTRACT(HOUR ...)` / day-of-week, or the answers
  are wrong by the local offset.
- **Optional date params are null-guarded.** Every dated query uses
  `(:date_from IS NULL OR ts >= :date_from)` — never bare `ts >= :date_from`.
- **Reuse the existing slowapi rate limiter** on `/agent/message`. Agent turns cost real
  money; the limiter and its Redis store already exist.
- **Re-run `scripts/provision_agent_role.py` after every `docker compose down -v`.**
  The `agent_readonly` role is cluster-level and is not recreated by schema migrations.
  Phase 7's `run_sql_query` tool depends on this role existing.

---

## Commands

```bash
# Bring the full stack up (FastAPI, Celery worker, Beat, Redis, Postgres, Flower)
docker compose up --build

# Schema change (agent tables / role) requires a fresh volume
docker compose down -v && docker compose up --build

# Provision the read-only agent role (idempotent; run after DB is up)
docker compose exec api python scripts/provision_agent_role.py

# Run the full test suite
docker compose exec api pytest

# Run only agent tests
docker compose exec api pytest tests/test_agent_loop.py tests/test_tools.py \
  tests/test_agent_memory.py tests/test_agent_api.py

# Run the eval harness
docker compose exec api python -m eval.runner --run-name "haiku-baseline" --model Codex-haiku-4-5
docker compose exec api python -m eval.runner --run-name "sonnet-comparison" --model Codex-sonnet-4-6

# Frontend dev server
cd frontend && npm run dev
```

Local environment is **Windows** (VS Code, DBeaver, Postman). Postgres: `localhost:5432`,
user `krishsomi021`, db `jobsdb`.

---

## Coding Standards

- Type hints everywhere on the Python side; Pydantic v2 models for API boundaries.
- Tools, the loop, and memory are **pure and FastAPI-free** where possible (`loop.py` has no
  FastAPI imports) so they're unit-testable without the web layer.
- Every tool returns a `ToolResult(success, data, error, chart_spec)` — tool errors never
  raise out of the loop; they come back as `success=False` and the loop continues.
- New env vars: add to pydantic-settings **and** document in `.env.example` and `PRD.md`/`ARCHITECTURE.md`.
- Tests accompany each phase (see `tasks.md`). Don't advance a phase with red tests.

---

## Known Local-vs-CI Tradeoff

5 stats/list tests fail locally when run against a populated dev DB and pass in CI against a
clean DB. This is expected. Do not "fix" them by coupling assertions to dev data.

---

## Do Not Touch (this branch)

- `handoff.md` / `.claudeignore` — separate cleanup task.
- The fraud model path, Celery reliability layer, or existing recommender internals — the
  agent **wraps** these, it does not modify them.
- `task_acks_late`, the schema-ownership rule, or the Compose project name.
