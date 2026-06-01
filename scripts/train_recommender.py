"""
Train a Spotify recommendation model from personal listening history.

Usage:
    python scripts/train_recommender.py ./data/spotify_export
    python scripts/train_recommender.py ./data/spotify_export --output models/spotify_recommender.joblib --min-plays 2

Detects basic export (StreamingHistory*.json) or extended export
(Streaming_History_Audio_*.json) automatically.

Produces models/spotify_recommender.joblib containing:
  - matrix       : cosine-similarity matrix (n_tracks × n_tracks, float32)
  - track_ids    : list of track_id strings (row/col index)
  - track_id_to_idx : dict track_id → int index
  - track_lookup : dict track_id → {"track_name": ..., "artist": ...}
"""

import argparse
import glob
import hashlib
import json
import logging
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def make_track_id(artist: str, track_name: str) -> str:
    key = f"{artist.strip().lower()}|||{track_name.strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def load_basic_export(folder: Path) -> pd.DataFrame:
    files = sorted(glob.glob(str(folder / "StreamingHistory*.json")))
    rows = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            rows.extend(json.load(fh))
    df = pd.DataFrame(rows)
    df = df.rename(columns={
        "trackName": "track_name",
        "artistName": "artist",
        "msPlayed": "ms_played",
        "endTime": "timestamp",
    })
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df[["timestamp", "track_name", "artist", "ms_played"]]


def load_extended_export(folder: Path) -> pd.DataFrame:
    files = sorted(glob.glob(str(folder / "Streaming_History_Audio_*.json")))
    rows = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            rows.extend(json.load(fh))
    df = pd.DataFrame(rows)
    df = df.rename(columns={
        "master_metadata_track_name": "track_name",
        "master_metadata_album_artist_name": "artist",
        "ms_played": "ms_played",
        "ts": "timestamp",
    })
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.dropna(subset=["track_name", "artist"])
    return df[["timestamp", "track_name", "artist", "ms_played"]]


def build_model(df: pd.DataFrame, min_plays: int) -> dict:
    df = df[df["ms_played"] >= 30_000].copy()
    logger.info("%d plays after filtering skips (< 30s)", len(df))

    df["track_id"] = df.apply(
        lambda r: make_track_id(r["artist"], r["track_name"]), axis=1
    )

    track_lookup = (
        df[["track_id", "track_name", "artist"]]
        .drop_duplicates("track_id")
        .set_index("track_id")[["track_name", "artist"]]
        .to_dict("index")
    )

    play_counts = df.groupby("track_id")["ms_played"].count()
    valid_tracks = play_counts[play_counts >= min_plays].index
    df = df[df["track_id"].isin(valid_tracks)]
    logger.info("%d tracks with >= %d plays", len(valid_tracks), min_plays)

    if len(valid_tracks) < 2:
        logger.error(
            "Not enough tracks to build a model (need at least 2 with >= %d plays). "
            "Try lowering --min-plays.",
            min_plays,
        )
        sys.exit(1)

    # Track × day matrix: each cell = total ms played for that track on that day.
    # Cosine similarity between rows captures tracks that are played on the same days.
    df["date"] = df["timestamp"].dt.date
    pivot = (
        df.groupby(["track_id", "date"])["ms_played"]
        .sum()
        .unstack(fill_value=0)
    )
    track_ids = list(pivot.index)
    matrix = pivot.values.astype(np.float32)

    logger.info("Computing similarity matrix (%d tracks)...", len(track_ids))
    sim_matrix = cosine_similarity(matrix).astype(np.float32)
    np.fill_diagonal(sim_matrix, 0.0)

    track_id_to_idx = {tid: i for i, tid in enumerate(track_ids)}

    return {
        "matrix": sim_matrix,
        "track_ids": track_ids,
        "track_id_to_idx": track_id_to_idx,
        "track_lookup": track_lookup,
    }


def print_sample(artifact: dict) -> None:
    track_ids = artifact["track_ids"]
    matrix = artifact["matrix"]
    track_lookup = artifact["track_lookup"]
    track_id_to_idx = artifact["track_id_to_idx"]

    if not track_ids:
        return

    seed = track_ids[0]
    info = track_lookup.get(seed, {})
    logger.info(
        "Sample seed: '%s — %s'",
        info.get("artist", "?"),
        info.get("track_name", "?"),
    )
    idx = track_id_to_idx[seed]
    scores = matrix[idx]
    top5 = np.argsort(scores)[::-1][:5]
    for rank, i in enumerate(top5, 1):
        rec_info = track_lookup.get(track_ids[i], {})
        logger.info(
            "  %d. %s — %s  (score=%.4f)",
            rank,
            rec_info.get("artist", "?"),
            rec_info.get("track_name", "?"),
            scores[i],
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Spotify recommendation model")
    parser.add_argument(
        "folder",
        nargs="?",
        default="./data/spotify_export",
        help="Path to Spotify export folder (default: ./data/spotify_export)",
    )
    parser.add_argument(
        "--output",
        default="models/spotify_recommender.joblib",
        help="Output path for the joblib artifact",
    )
    parser.add_argument(
        "--min-plays",
        type=int,
        default=2,
        help="Minimum play count to include a track (default: 2)",
    )
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        logger.error("Export folder not found: %s", folder)
        sys.exit(1)

    extended_files = glob.glob(str(folder / "Streaming_History_Audio_*.json"))
    basic_files = glob.glob(str(folder / "StreamingHistory*.json"))

    if extended_files:
        logger.info("Detected extended export format (%d file(s))", len(extended_files))
        df = load_extended_export(folder)
    elif basic_files:
        logger.info("Detected basic export format (%d file(s))", len(basic_files))
        df = load_basic_export(folder)
    else:
        logger.error(
            "No Spotify export files found in %s. "
            "Expected StreamingHistory*.json or Streaming_History_Audio_*.json.",
            folder,
        )
        sys.exit(1)

    logger.info("Loaded %d raw plays", len(df))

    artifact = build_model(df, min_plays=args.min_plays)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output_path)

    logger.info("Saved model → %s", output_path)
    logger.info("Tracks in model: %d", len(artifact["track_ids"]))
    print_sample(artifact)


if __name__ == "__main__":
    main()
