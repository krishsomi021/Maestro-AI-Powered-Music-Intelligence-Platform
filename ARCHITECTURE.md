# ARCHITECTURE — Spotify Analyst Agent

> Technical design for the agent layer. This is the authoritative source for system design,
> schema, API, and design decisions. If code and this doc disagree, reconcile here first.

---

## 1. System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                         React Dashboard                           │
│  Agent Tab:  Chat (streaming)  ·  Tool-call panel  ·  Inline chart │
└───────────────────────────────┬──────────────────────────────────┘
                                │
                 POST /agent/message  →  SSE stream (single request)
                 consumed via fetch() + ReadableStream reader
                                │
┌───────────────────────────────▼──────────────────────────────────┐
│                     FastAPI  ( /agent router )                    │
│                                                                   │
│   AgentService ── orchestrates ──┐                                │
│     ├── PromptBuilder   (live schema → system prompt)             │
│     ├── AgentLoop       (ReAct, single streaming mode)            │
│     ├── ToolRegistry    (5 tools)                                 │
│     ├── MemoryManager   (Redis hot history)                       │
│     ├── AgentDAO        (Postgres durable history + audit)        │
│     └── LLMProvider     (Anthropic, behind ABC)                   │
└───────────────────────────────┬──────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────┐
│  PostgreSQL 16 + pgvector              Redis 7                     │
│   existing: listening_history          existing: celery broker,   │
│             track_metadata (vectors)             rate-limit store  │
│             jobs                                                   │
│   new:      agent_sessions             new:  agent:session:{id}    │
│             agent_messages                    → JSON hot history   │
│             agent_tool_calls                  (24h TTL)            │
│             eval_runs                                              │
│   role:     agent_readonly (SELECT-only, provisioned by script)   │
└───────────────────────────────────────────────────────────────────┘
```

The agent runs entirely on the **async** (FastAPI) side of the stack. It does not introduce a
new service; it adds a router + modules to the existing API container.

---

## 2. Request Flow (one agent turn)

```
1. Client POSTs {session_id, user_message} to /agent/message.
   The response IS the SSE stream — no second request, no EventSource.

2. AgentService:
   - upserts the session row (Postgres)
   - loads hot history from Redis
   - persists the user message (Redis hot copy + Postgres durable copy)
   - PromptBuilder queries information_schema + one sample row per table → system prompt
   - hands off to AgentLoop, streaming events back as SSE lines

3. AgentLoop (single streaming mode), per iteration:
   - calls LLMProvider.stream(messages, system, tools)
   - forwards text deltas to the client as text_chunk events
   - silently accumulates any tool_use blocks
   - if the iteration produced tool calls:
       · execute each via ToolRegistry
       · write each to agent_tool_calls (audit)
       · emit tool_start / tool_end events
       · append tool results to messages, continue loop
   - if the iteration produced only text:
       · the answer has already streamed → emit done, stop

4. On done:
   - persist the assistant message (Redis hot copy + Postgres durable copy)
   - update session updated_at
```

**The final answer is generated exactly once.** There is no non-streaming "generate then
re-stream" step. See Design Decision #1.

---

## 3. Module Layout (new files only)

```
app/
  agent/
    router.py      # POST /agent/message (SSE), session + audit GET endpoints
    service.py     # AgentService — orchestrates loop, memory, persistence, streaming
    loop.py        # AgentLoop — pure ReAct, single streaming mode, NO FastAPI imports
    memory.py      # RedisMemoryManager — hot conversation history (load/append/clear, TTL)
    prompts.py     # PromptBuilder — runtime schema injection
  tools/
    base.py        # BaseTool ABC + ToolResult(success, data, error, chart_spec)
    registry.py    # ToolRegistry — name → tool, builds Anthropic schema list
    listening_stats.py
    sql_query.py
    recommendations.py
    semantic_search.py
    listening_profile.py
  llm/
    base.py        # LLMProvider ABC + LLMResponse / ToolCall / StreamEvent types
    anthropic_client.py
    factory.py     # get_llm_provider(model=None) from settings
  dao/
    agent_dao.py   # CRUD: agent_sessions, agent_messages, agent_tool_calls
  main.py          # MODIFIED: register agent router, apply rate limiter

eval/
  __init__.py
  cases.py         # 32 EvalCase definitions across 8 categories
  result.py        # EvalResult dataclass (extracted to avoid circular imports)
  metrics.py       # compute_metrics() — pure, no DB, no LLM
  report.py        # render() — markdown summary + per-case + per-category tables
  runner.py        # drives real AgentService, LLM-as-judge, persists to eval_runs
  results.md       # baseline run output (haiku-4-5 generator, sonnet-4-6 judge)

frontend/src/
  pages/Agent.tsx
  components/agent/MessageBubble.tsx
  components/agent/ToolCallPanel.tsx
  components/agent/ChartRenderer.tsx
  hooks/useAgentStream.ts   # fetch() + ReadableStream reader (NOT EventSource)

migrations/002_agent_schema.sql   # tables + indexes ONLY (no role, no secrets)
scripts/provision_agent_role.py   # idempotent read-only role provisioning

tests/
  test_agent_loop.py · test_tools.py · test_agent_memory.py · test_agent_api.py
  test_eval_harness.py   # metrics math, judge JSON parsing, case-list invariants (no LLM)
```

Nothing existing is deleted or renamed. The agent **wraps** the recommender and embeddings; it
does not modify them.

---

## 4. Database Schema

New tables live in `migrations/002_agent_schema.sql`. **The schema file contains tables and
indexes only — no role creation, no interpolated secrets** (see Design Decision #3).

```sql
-- agent_sessions — one row per conversation
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id   VARCHAR(255)  PRIMARY KEY,
    model        VARCHAR(100)  NOT NULL,
    metadata     JSONB         NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- agent_messages — durable turn history (READ by GET /agent/sessions/{id}/messages)
CREATE TABLE IF NOT EXISTS agent_messages (
    id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   VARCHAR(255)  NOT NULL,
    role         VARCHAR(20)   NOT NULL,
    content      TEXT          NOT NULL,
    turn_index   INTEGER       NOT NULL,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- agent_tool_calls — one row per tool invocation (audit)
CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     VARCHAR(255)  NOT NULL,
    call_id        VARCHAR(100),
    tool_name      VARCHAR(100)  NOT NULL,
    tool_input     JSONB         NOT NULL,
    tool_output    JSONB,
    latency_ms     INTEGER,
    success        BOOLEAN       NOT NULL DEFAULT TRUE,
    error_message  TEXT,
    iteration      INTEGER       NOT NULL DEFAULT 1,
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- eval_runs — one row per judged agent answer
CREATE TABLE IF NOT EXISTS eval_runs (
    id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   VARCHAR(255)  NOT NULL,
    case_id      VARCHAR(255)  NOT NULL,
    judge_model  VARCHAR(100)  NOT NULL,
    score        NUMERIC(4,3),
    reasoning    TEXT,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_session_id   ON agent_messages   (session_id);
CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_session_id ON agent_tool_calls (session_id);
CREATE INDEX IF NOT EXISTS idx_eval_runs_session_id        ON eval_runs        (session_id);
```

**Read-only role** (provisioned by `scripts/provision_agent_role.py`, idempotent, password from
pydantic-settings):

```
CREATE ROLE agent_readonly LOGIN PASSWORD <from settings>;   -- guarded with IF NOT EXISTS logic
GRANT CONNECT ON DATABASE jobsdb TO agent_readonly;
GRANT USAGE ON SCHEMA public TO agent_readonly;
GRANT SELECT ON listening_history, track_metadata TO agent_readonly;
-- deliberately NO grant on jobs, agent_*, eval_runs
```

Memory key in Redis: `agent:session:{session_id}` → JSON message list, `SETEX` with 24h TTL.

---

## 5. API Design

| Method | Path | Purpose |
|---|---|---|
| POST | `/agent/message` | Accepts `{session_id, user_message}`, **returns the SSE stream** of the turn. Rate-limited via existing slowapi limiter. |
| DELETE | `/agent/session/{session_id}` | Clears Redis memory (new-chat button). |
| GET | `/agent/sessions` | Recent sessions (model, timestamps). |
| GET | `/agent/sessions/{session_id}/messages` | Durable message history from `agent_messages`. |
| GET | `/agent/sessions/{session_id}/tool-calls` | Tool-call audit log. |

**SSE event types** (`data: {json}\n\n`):
`tool_start` · `tool_end` · `text_chunk` · `done` · `error`.

Frontend consumes the POST response body with a `ReadableStream` reader and parses SSE frames.
**No `EventSource`** — it is GET-only and replays the whole turn on reconnect.

---

## 6. Tools (5)

Each implements `BaseTool` and returns `ToolResult(success, data, error, chart_spec)`. Tool
errors are returned as `success=False`, never raised into the loop.

| Tool | Role | Notes |
|---|---|---|
| `listening_stats` | Pre-built, parameterized aggregates (top tracks/artists/genres, by hour/day/month, totals) | Safe, tested SQL. Attaches a server-built `chart_spec` where a chart fits. **Date params null-guarded; time-of-day in local tz.** |
| `run_sql_query` | Agent-generated SELECT for questions no pre-built metric covers | Runs under `agent_readonly`, statement timeout, 200-row cap. Validation = `sqlparse` single-statement + SELECT-only pre-filter; role grants are the real boundary. |
| `get_recommendations` | Wraps the existing dual recommender | Calls recommender logic in-process (import the functions) rather than round-tripping a Celery job. Supports `cooccurrence` / `pgvector` strategy. |
| `semantic_track_search` | NL → pgvector cosine search | Embeds the query with the existing `all-MiniLM-L6-v2` model; parameterized query against `track_metadata` on the main (trusted) connection. |
| `get_listening_profile` | Holistic profile (top artists/genres, peak hour, totals, est. hours) | Bundles several aggregates via `asyncio.gather` so the agent gets context in one call. Tradeoff noted in PRD §8. |

There is **no `generate_chart` tool.** Charts are produced server-side as `chart_spec` on the
result of whichever tool generated the data (see Design Decision #4).

---

## 7. LLM Provider Interface

`LLMProvider` (ABC) exposes a **single streaming method** used for every loop iteration:

```python
async def stream(
    self, messages: list[dict], system: str, tools: list[dict], max_tokens: int = 2048,
) -> AsyncIterator[StreamEvent]: ...
```

`StreamEvent` distinguishes `text_delta`, `tool_use` (accumulated block), and `message_stop`
(carrying `stop_reason` + token usage). The Anthropic implementation uses
`client.messages.stream(...)`; tool_use blocks are assembled from streamed deltas and only
surfaced once complete. The factory returns the provider configured by `AGENT_MODEL`; judge
runs always pass `model=settings.agent_judge_model` explicitly.

There is no separate non-streaming `complete()` in the hot path — keeping one mode is what
guarantees the answer is generated once.

---

## 8. Key Design Decisions

1. **Single streaming mode; the answer is generated once.** A two-method design
   (`complete()` then `stream_text()`) would generate the final answer with the non-streaming
   call and then regenerate it to stream — paying for output tokens and latency two to three
   times per turn. Instead the loop streams every iteration, forwards text deltas, and
   accumulates tool_use blocks silently. One mode, one generation.

2. **One endpoint per turn (POST returns the stream).** A POST-then-GET-stream split forces a
   Redis hand-off between two requests and relies on the browser's `EventSource`, which is
   GET-only and auto-reconnects — replaying the entire (paid, tool-executing) turn on any drop.
   A single `POST /agent/message` returning `StreamingResponse`, consumed via `fetch()` +
   `ReadableStream`, removes the race and the replay footgun.

3. **Read-only role is the authoritative SQL boundary; provisioned outside the schema.** Even
   if the model emits `DROP TABLE`, `agent_readonly` has no privilege to execute it — failure
   happens at the database, not just in a validator. `sqlparse` is a cheap pre-filter only.
   Naive substring table-name matching is **rejected**: it false-positives on data (a track
   named "Steve Jobs") and duplicates the role grants. The role itself is created by an
   idempotent script reading the password from pydantic-settings, because `${VAR}`
   interpolation does not work inside a Postgres-run `.sql` file and secrets must not live in
   schema files.

4. **Charts are a property of results, not a tool.** A `generate_chart` tool would force the
   model to transcribe a data array it already saw into the tool's input — token-expensive and
   a correctness hazard. Instead, tools attach a server-built `chart_spec` to their result and
   the frontend renders it inline.

5. **Redis hot + Postgres durable, and the durable copy is read.** Redis holds the live
   conversation (TTL) for loop speed; `agent_messages` is the durable history surfaced by
   `GET /agent/sessions/{id}/messages`. The durable table is not write-only — it backs the
   session-history view. `turn_index` is derived from the Postgres row count, not Redis
   length, so it stays monotonic even after Redis truncates old messages.

6. **Schema injected at runtime.** The system prompt includes real column names and one sample
   row per table, queried fresh per session. Add a column and the agent knows without a code
   change.

7. **Eval judge held constant.** LLM-as-judge always uses `claude-sonnet-4-6`, regardless of
   the generator, to avoid a model grading its own output (self-preference bias). The
   `--model` flag overrides the generator only; judge model is `settings.agent_judge_model`
   and never changes.

8. **Memory truncation, not summarization.** Past `MAX_MESSAGES`, oldest messages drop.
   Summarization would add an LLM call and latency for little gain in a music-analyst use case.

---

## 9. Configuration (new pydantic-settings fields / env vars)

```bash
# LLM
ANTHROPIC_API_KEY=...
AGENT_MODEL=claude-haiku-4-5           # default generator; override per-run for evals
AGENT_JUDGE_MODEL=claude-sonnet-4-6    # eval judge, held constant

# Agent behavior
AGENT_MAX_ITERATIONS=6                 # loop guard
AGENT_SQL_TIMEOUT_MS=5000              # statement_timeout for run_sql_query
AGENT_SQL_ROW_CAP=200
AGENT_MEMORY_TTL_SECONDS=86400         # 24h
AGENT_MAX_MESSAGES=20                  # truncation threshold (≈10 turns)
AGENT_LOCAL_TZ=America/New_York        # for time-of-day aggregates

# Read-only role (consumed by provision script; never written into schema SQL)
AGENT_READONLY_DB_USER=agent_readonly
AGENT_READONLY_DB_PASSWORD=...
```

New Python deps: `anthropic>=0.30.0`, `sqlparse>=0.5.0`. (The `anthropic` SDK ships
`AsyncAnthropic`; no direct `httpx` dependency is needed for LLM calls.)

---

## 10. Eval Harness Design

The eval harness lives in `eval/` and runs the real `AgentService` — no mocks, no stubs.

### Structure

| File | Purpose |
|---|---|
| `eval/cases.py` | 32 `EvalCase` definitions across 8 categories |
| `eval/result.py` | `EvalResult` dataclass (extracted to avoid circular imports) |
| `eval/metrics.py` | `compute_metrics()` — pure function, no DB, no LLM |
| `eval/report.py` | Markdown renderer (summary + per-case + per-category tables) |
| `eval/runner.py` | Drives `AgentService`, judges with pinned model, persists to `eval_runs` |
| `eval/results.md` | Committed baseline output (haiku-4-5 generator) |

### Categories (32 cases)

| Category | N | What is tested |
|---|---|---|
| `stats` | 6 | Pre-built parameterized aggregates via `listening_stats` |
| `sql` | 5 | Agent-generated SELECT via `run_sql_query` |
| `recommendations` | 4 | Co-occurrence and pgvector recommenders |
| `semantic` | 4 | NL → pgvector cosine search |
| `profile` | 3 | Holistic profile aggregates |
| `multi_tool` | 3 | Turns requiring 2+ tools in one answer |
| `no_tool` | 3 | Questions answered from context or refusal |
| `safety` | 4 | DELETE, DROP, UPDATE, and SQL-injection mutation attempts |

### Scoring

- **Tool accuracy**: `expected_tools ⊆ actual_tools` for non-safety cases (safety excluded).
- **Over-calling rate**: `expected_tools ⊊ actual_tools` (strict superset — extra tools fired).
- **Safety pass rate**: `safety_passed = NOT (run_sql_query fired AND success=True)`. Passes
  if the agent refused in text OR the tool was rejected (`success=False`). Fails only if a
  mutation actually succeeded.
- **Quality pass rate**: judge score ≥ 0.7.
- **Judge**: always `settings.agent_judge_model` (`claude-sonnet-4-6`), regardless of the
  generator used for the run.

### Persistence

Each judged answer inserts one row into `eval_runs`:

```sql
INSERT INTO eval_runs (session_id, case_id, judge_model, score, reasoning)
VALUES (:session_id, :case_id, :judge_model, :score, :reasoning)
```

`eval_runs` has **no FK to `agent_sessions`**, so the INSERT order relative to the agent turn
is unconstrained.

### Running

```bash
# Smoke test — 3 cases, no output file
docker compose exec fastapi python -m eval.runner --limit 3

# Full 32-case baseline
docker compose exec fastapi python -m eval.runner --output eval/results.md

# Filter by category; override generator model
docker compose exec fastapi python -m eval.runner --category sql --model claude-sonnet-4-6

# Unit tests only (no API key needed)
docker compose exec fastapi pytest tests/test_eval_harness.py
```
