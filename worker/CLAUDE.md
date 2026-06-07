# Worker rules

- Use sync SQLAlchemy only (`worker/db/sync_session.py`). Never use async sessions here.
- `task_acks_late=True` must remain set.
- All tasks inherit from `BaseJobTask` for retry, dead-letter, and cancellation behavior.
- `run_ml_inference` is dual-mode: `features` in payload → fraud model; `seed_tracks` in payload → Spotify recommender.
