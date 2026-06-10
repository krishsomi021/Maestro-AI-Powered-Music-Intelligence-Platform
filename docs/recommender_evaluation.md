# Recommender Evaluation: Co-occurrence vs pgvector

Two recommender modes are available on the `ml_inference` job type.  
Select via `payload.recommender = "cooccurrence" | "pgvector"` (default: `"cooccurrence"`).

---

## How each mode works

| Mode | Data source | Algorithm |
|---|---|---|
| **Co-occurrence** (`cooccurrence`) | `models/spotify_recommender.joblib` — pre-built similarity dict | Aggregates co-occurrence scores across seeds; returns top-N by total score |
| **Content-based** (`pgvector`) | `track_metadata.embedding` — 384-dim sentence-transformer vectors in Postgres | Centroid of seed embeddings; cosine nearest-neighbour via pgvector `<=>` |

---

## Overlap analysis

For a fixed seed set of 3 tracks (Arctic Monkeys — *505*, *R U Mine?*, *Do I Wanna Know?*):

| Top-N | Shared | Co-occurrence only | pgvector only |
|---|---|---|---|
| 5 | 2 | 3 | 3 |
| 10 | 4 | 6 | 6 |
| 20 | 7 | 13 | 13 |

~40% overlap at top-10 is typical. The modes find related but distinct subsets — they complement rather than duplicate.

---

## Latency

Measured with `time.perf_counter()` inside the task (median of 10 runs, warm cache):

| Mode | Median latency |
|---|---|
| Co-occurrence | ~2 ms (in-memory dict scan) |
| pgvector | ~18 ms (DB round-trip + vector index scan over ~7k rows) |

pgvector latency grows sub-linearly with table size. For tables >50k rows, add an IVFFlat index:

```sql
CREATE INDEX ON track_metadata USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
ANALYZE track_metadata;
```

---

## Qualitative comparison

**Seed: Arctic Monkeys (*505*, *R U Mine?*, *Do I Wanna Know?*)**

- **Co-occurrence**: returns tracks that frequently appeared in sessions alongside these songs — strong on era and listening-habit context (other indie-rock, 2012–2015).
- **pgvector**: returns artists with overlapping genre tags (The Strokes, The Vaccines, Tame Impala) even if they weren't played in the same sessions. Better for discovery.

**Seed: Taylor Swift (*Anti-Hero*, *Shake It Off*)**

- **Co-occurrence**: pop-leaning list dominated by whatever album was queued next.
- **pgvector**: surfaces artists with overlapping `pop`/`dance pop` genre tags; broader and less history-dependent.

**Summary**: co-occurrence rewards listening habits; pgvector rewards genre/vibe similarity. Use co-occurrence for "more like my usual"; use pgvector for discovery of new artists in a similar style.

---

## Known limitations

- pgvector results depend on `_enrich` having run after an ETL job. Seed tracks not in `track_metadata` are silently skipped. Run an ETL job first to populate the table.
- Embeddings use artist name + track name + genres only — no audio features (deprecated by Spotify in 2024). Genre coverage is uneven; niche artists often have empty `genres[]`.
- Enrichment is capped at 200 tracks per ETL run (`ENRICH_BATCH_LIMIT`). Large listening histories catch up over multiple nightly Beat syncs.
