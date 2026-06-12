# PRD — Spotify Analyst Agent

> What we're building, who it's for, the problem it solves, and how we'll know it works.

---

## 1. Summary

An LLM-powered natural-language analyst that operates over personal Spotify listening data
already loaded into PostgreSQL by the platform's ETL pipeline. A user asks questions in plain
English; a custom agent loop decides which tools to call, executes them against live data, and
streams a structured answer (with inline charts) back to a React dashboard.

It is a **new capability layer on the existing Distributed Job Processing Platform**, not a
standalone app. It reuses the platform's PostgreSQL (with pgvector embeddings), Redis, FastAPI
app, and Docker Compose stack.

---

## 2. Why This Exists

**Portfolio purpose.** The platform already demonstrates distributed job processing, ML
inference, and ETL. This layer demonstrates applied LLM/agent engineering on top of real data
the system already owns — the arc "I built a backend platform, then built an analyst agent as a
capability layer on it" reads as architectural maturity, not a second disconnected project.

**What makes it credible (the differentiators that must survive interview scrutiny):**

- A **custom ReAct-style agent loop** — no LangChain / LlamaIndex. Every line of the
  plan → tool → observe → respond cycle is owned and testable.
- A **provider-agnostic LLM interface** — Anthropic or OpenAI behind one ABC; switching is a
  config change.
- A **schema-aware system prompt** generated at runtime from the live database, so the agent's
  table/column knowledge is never stale.
- A **role-first SQL safety layer** — agent-generated SQL runs under a read-only PostgreSQL
  role with a hard statement timeout.
- A **tool-call audit trail** in PostgreSQL — every invocation (name, input, output, latency,
  success) is queryable after the fact.
- **Token-by-token streaming** over SSE to the dashboard.
- A real **evaluation framework** — ground-truth Q&A, tool-selection accuracy, LLM-as-judge,
  and latency benchmarks. This is the piece that reads as ML engineering rather than a demo.

---

## 3. Users

| User | Context | What they need |
|---|---|---|
| **Primary: the data owner** (Krishna) | Has years of Spotify export data loaded | Ask arbitrary questions about listening behavior and get fast, specific, charted answers without writing SQL |
| **Secondary: a portfolio reviewer / interviewer** | Clicks through the live demo | See the agent reason, call tools transparently, stream answers, and render charts — and read the design decisions behind it |

There is no multi-tenant requirement. Sessions are per-browser, identified by a client-generated
session id. Auth is out of scope for v1 (single-user local/demo deployment), but the agent
endpoint **is** rate-limited.

---

## 4. The Problem It Solves

Today, answering a question like *"how has my listening shifted over the last year, and what
should I play next?"* requires hand-writing SQL against `listening_history` / `track_metadata`,
running a separate recommender, and manually charting the result. There is no conversational
surface, no follow-up memory, and no way to ask in natural language.

The agent collapses that into a single chat turn: it picks the right tool(s), runs them against
live data, remembers the conversation so follow-ups resolve ("which of those has the most
plays?"), and renders the answer with a chart inline.

---

## 5. What the User Can Do (core interactions)

1. **Ask an aggregate question** — "Who are my top 10 artists of all time?" → agent calls
   `listening_stats`, answers with numbers and a bar chart.
2. **Ask a custom analytical question** — "What percentage of my plays were skipped?" → agent
   writes a safe SELECT via `run_sql_query`, returns the figure.
3. **Describe music by vibe** — "Find me melancholy late-night tracks" → `semantic_track_search`
   over pgvector embeddings.
4. **Ask for recommendations** — "What should I listen to next?" → `get_recommendations`
   wrapping the existing dual recommender.
5. **Ask an open-ended preference question** — "What kind of music am I into?" →
   `get_listening_profile` gives the agent holistic context first.
6. **Ask follow-ups** — conversation memory persists within a session, so pronoun/reference
   follow-ups resolve against prior turns.
7. **See the agent's work** — a collapsible "Agent used: …" panel shows which tools ran, with
   inputs/outputs available on expand.
8. **Start a new chat** — clears session memory.
9. **Review session history** — past sessions, their messages, and their tool-call audit logs
   are retrievable.

---

## 6. Scope

### In scope (v1)
- 5 tools: `listening_stats`, `run_sql_query`, `get_recommendations`, `semantic_track_search`,
  `get_listening_profile`.
- Streaming chat with inline charts (charts come from tool-result `chart_spec`, server-built).
- Redis session memory (hot) + PostgreSQL durable message/tool-call/session history.
- Read-only role SQL safety + statement timeout.
- Evaluation harness (30–50 cases) with tool accuracy, judge score, latency, and a
  haiku-vs-sonnet comparison.
- A new "Agent" tab in the existing dashboard.

### Out of scope (v1)
- Authentication / multi-tenant isolation.
- A standalone `generate_chart` tool (charts are a property of results, not a tool).
- Conversation summarization (memory uses truncation, not summarization — deliberate tradeoff).
- Writes of any kind from agent SQL (read-only by construction).
- Live Spotify Web API calls inside an agent turn (the agent reads already-loaded data).

---

## 7. Success Criteria

The feature is "done" when:

- All five tools are unit-tested in isolation.
- The agent loop handles 0, 1, and 2+ tool calls per turn correctly and terminates within
  `AGENT_MAX_ITERATIONS`.
- The final answer is generated **exactly once** and streamed token-by-token to the dashboard.
- Charts render inline in chat from tool-result specs.
- Conversation memory makes follow-ups resolve within a session.
- `run_sql_query` rejects non-SELECT statements at **both** the validation layer and the
  database (read-only role) level.
- The tool-call audit table populates on every run and is queryable via the API.
- The eval harness runs 30+ cases and reports tool-selection accuracy, LLM-as-judge score
  (judged by `claude-sonnet-4-6`), and p50/p95 latency, with a haiku-vs-sonnet table.
- `docker compose up` brings up the full system including the agent endpoint.
- README + `docs/architecture.md` updated; a demo GIF shows a 3+ turn conversation with a chart
  and the tool-call panel.

---

## 8. Non-Goals / Explicit Tradeoffs (document these in the README)

- **Truncation over summarization** for memory — older turns are rarely needed for a music
  analyst; summarization adds latency and an extra LLM call.
- **Two overlapping query tools on purpose** — `listening_stats` is the safe, fast, tested path;
  `run_sql_query` is the flexible escape hatch. The system prompt steers the agent to prefer the
  pre-built metric when one fits. This overlap is the main place to watch in eval tool-accuracy.
- **`get_listening_profile` as a distinct tool** — it bundles several aggregates into one call
  to avoid the agent burning iterations on six sequential `listening_stats` calls. It could be a
  `listening_stats` metric instead; kept separate for the latency win.
