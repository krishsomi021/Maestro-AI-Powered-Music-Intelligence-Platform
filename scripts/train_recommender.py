"""
Train a Spotify co-occurrence recommendation model from personal export data.

Usage:
    python scripts/train_recommender.py ./data/spotify_export

Reads:
    StreamingHistory_music_0.json, StreamingHistory_music_1.json
    YourLibrary.json
    Playlist1.json

Produces:
    models/spotify_recommender.joblib

Model schema:
    {
        "similarity"   : dict[track_key, dict[track_key, float]],
        "uri_to_meta"  : dict[uri, {"track_name": str, "artist_name": str, "uri": str}],
        "saved_tracks" : set[uri],
        "trained_at"   : str (ISO-8601),
        "total_tracks" : int,
        "total_plays"  : int,
    }
"""

import argparse
import glob
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import joblib

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Gap between consecutive plays that starts a new session
SESSION_GAP = timedelta(minutes=30)
# Minimum play duration to count (skip short plays)
MIN_MS_PLAYED = 30_000


# ---------------------------------------------------------------------------
# Track key helpers
# ---------------------------------------------------------------------------

def _fallback_key(artist: str, track: str) -> str:
    return f"{artist.strip().lower()}|||{track.strip().lower()}"


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_streaming_history(folder: Path) -> list[dict]:
    """Load all StreamingHistory_music_*.json files and return raw records."""
    files = sorted(glob.glob(str(folder / "StreamingHistory_music_*.json")))
    records = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            records.extend(json.load(fh))
    return records


def _load_your_library(folder: Path) -> set:
    """Return set of saved-track URIs from YourLibrary.json."""
    path = folder / "YourLibrary.json"
    if not path.exists():
        logger.warning("YourLibrary.json not found — skipping saved-track signal")
        return set()
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return {t["uri"] for t in data.get("tracks", []) if t.get("uri")}


def _load_playlists(folder: Path) -> list[list[str]]:
    """Return list of playlists; each playlist is a list of trackUris."""
    path = folder / "Playlist1.json"
    if not path.exists():
        logger.warning("Playlist1.json not found — skipping playlist signal")
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    result = []
    for pl in data.get("playlists", []):
        uris = [
            item["track"]["trackUri"]
            for item in pl.get("items", [])
            if item.get("track") and item["track"].get("trackUri")
        ]
        if uris:
            result.append(uris)
    return result


# ---------------------------------------------------------------------------
# Session splitting
# ---------------------------------------------------------------------------

def _split_into_sessions(plays: list[tuple]) -> list[list[str]]:
    """
    plays: list of (datetime, track_key) sorted ascending by time.
    Returns list of sessions; each session is a list of track_keys.
    Consecutive plays within SESSION_GAP belong to the same session.
    """
    if not plays:
        return []
    sessions = []
    current = [plays[0][1]]
    for i in range(1, len(plays)):
        gap = plays[i][0] - plays[i - 1][0]
        if gap <= SESSION_GAP:
            current.append(plays[i][1])
        else:
            sessions.append(current)
            current = [plays[i][1]]
    sessions.append(current)
    return sessions


# ---------------------------------------------------------------------------
# Co-occurrence accumulation
# ---------------------------------------------------------------------------

def _add_cooccurrence(matrix: dict, a: str, b: str, weight: float = 1.0) -> None:
    if a == b:
        return
    matrix[a][b] = matrix[a].get(b, 0.0) + weight
    matrix[b][a] = matrix[b].get(a, 0.0) + weight


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_model(folder: Path) -> dict:
    # -----------------------------------------------------------------------
    # 1. Streaming history
    # -----------------------------------------------------------------------
    raw = _load_streaming_history(folder)
    total_plays_raw = len(raw)

    plays_filtered = []
    uri_to_meta: dict = {}

    for entry in raw:
        if entry.get("msPlayed", 0) < MIN_MS_PLAYED:
            continue
        artist = entry.get("artistName", "")
        track = entry.get("trackName", "")
        key = _fallback_key(artist, track)
        ts = datetime.strptime(entry["endTime"], "%Y-%m-%d %H:%M")
        plays_filtered.append((ts, key))

        # Streaming history has no URI — store metadata under the fallback key
        if key not in uri_to_meta:
            uri_to_meta[key] = {
                "track_name": track,
                "artist_name": artist,
                "uri": key,
            }

    skips = total_plays_raw - len(plays_filtered)
    logger.info("Streaming history: %d total plays, %d filtered as skips (<30 s)", total_plays_raw, skips)
    logger.info("Plays kept: %d", len(plays_filtered))

    # Sort by timestamp for session splitting
    plays_filtered.sort(key=lambda x: x[0])
    sessions = _split_into_sessions(plays_filtered)
    logger.info("Sessions: %d", len(sessions))

    # Build co-occurrence matrix from sessions
    cooccurrence: dict = defaultdict(dict)
    for session in sessions:
        unique = list(dict.fromkeys(session))  # preserve order, deduplicate within session
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                _add_cooccurrence(cooccurrence, unique[i], unique[j], weight=1.0)

    # -----------------------------------------------------------------------
    # 2. Saved tracks (YourLibrary.json) — weight multiplier 3.0
    # -----------------------------------------------------------------------
    saved_tracks = _load_your_library(folder)
    logger.info("Saved tracks: %d", len(saved_tracks))

    # Register saved-track URIs in uri_to_meta (YourLibrary has richer metadata)
    library_path = folder / "YourLibrary.json"
    if library_path.exists():
        with open(library_path, encoding="utf-8") as fh:
            lib_data = json.load(fh)
        for t in lib_data.get("tracks", []):
            uri = t.get("uri", "")
            if uri:
                uri_to_meta[uri] = {
                    "track_name": t.get("track", ""),
                    "artist_name": t.get("artist", ""),
                    "uri": uri,
                }

    # -----------------------------------------------------------------------
    # 3. Playlist co-occurrence (weight multiplier 2.0)
    # -----------------------------------------------------------------------
    playlists = _load_playlists(folder)
    logger.info("Playlists: %d", len(playlists))

    # Register playlist-track URIs in uri_to_meta
    pl_path = folder / "Playlist1.json"
    if pl_path.exists():
        with open(pl_path, encoding="utf-8") as fh:
            pl_data = json.load(fh)
        for pl in pl_data.get("playlists", []):
            for item in pl.get("items", []):
                t = item.get("track")
                if not t:
                    continue
                uri = t.get("trackUri", "")
                if uri and uri not in uri_to_meta:
                    uri_to_meta[uri] = {
                        "track_name": t.get("trackName", ""),
                        "artist_name": t.get("artistName", ""),
                        "uri": uri,
                    }

    for uris in playlists:
        unique = list(dict.fromkeys(uris))
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                _add_cooccurrence(cooccurrence, unique[i], unique[j], weight=2.0)

    # -----------------------------------------------------------------------
    # 4. Apply saved-track multiplier (3.0) to outbound scores
    # -----------------------------------------------------------------------
    for source in list(cooccurrence.keys()):
        if source in saved_tracks:
            cooccurrence[source] = {
                tgt: score * 3.0
                for tgt, score in cooccurrence[source].items()
            }

    # -----------------------------------------------------------------------
    # 5. Normalize: each source row divided by its total outbound score sum
    # -----------------------------------------------------------------------
    similarity: dict = {}
    for source, neighbours in cooccurrence.items():
        total = sum(neighbours.values())
        if total > 0:
            similarity[source] = {
                tgt: round(score / total, 6)
                for tgt, score in neighbours.items()
            }

    total_tracks = len(similarity)
    total_plays = len(plays_filtered)

    return {
        "similarity": similarity,
        "uri_to_meta": uri_to_meta,
        "saved_tracks": saved_tracks,
        "trained_at": datetime.utcnow().isoformat(),
        "total_tracks": total_tracks,
        "total_plays": total_plays,
        # internal stats used only by the training summary; not needed at inference
        "_total_plays_raw": total_plays_raw,
        "_skips": skips,
        "_num_playlists": len(playlists),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Train Spotify co-occurrence recommendation model")
    parser.add_argument(
        "folder",
        nargs="?",
        default="./data/spotify_export",
        help="Path to Spotify export folder (default: ./data/spotify_export)",
    )
    parser.add_argument(
        "--output",
        default="models/spotify_recommender.joblib",
        help="Output path (default: models/spotify_recommender.joblib)",
    )
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        logger.error("Export folder not found: %s", folder)
        sys.exit(1)

    streaming_files = glob.glob(str(folder / "StreamingHistory_music_*.json"))
    if not streaming_files:
        logger.error("No StreamingHistory_music_*.json files found in %s", folder)
        sys.exit(1)

    model = build_model(folder)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("---")
    logger.info("Total plays parsed:       %d", model["_total_plays_raw"])
    logger.info("Plays filtered as skips:  %d", model["_skips"])
    logger.info("Plays kept:               %d", model["total_plays"])
    logger.info("Unique tracks in model:   %d", model["total_tracks"])
    logger.info("Playlists processed:      %d", model["_num_playlists"])
    logger.info("Saved tracks:             %d", len(model["saved_tracks"]))
    logger.info("Model saved to:           %s", output_path)

    # Strip internal stats before writing — they don't belong in the inference artifact
    for key in ("_total_plays_raw", "_skips", "_num_playlists"):
        model.pop(key, None)
    joblib.dump(model, output_path)


if __name__ == "__main__":
    main()
