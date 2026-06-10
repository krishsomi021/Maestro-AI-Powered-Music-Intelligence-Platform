"""
Verify Spotify API credentials before enabling the pgvector enrichment pipeline.

Usage:
    SPOTIFY_CLIENT_ID=xxx SPOTIFY_CLIENT_SECRET=yyy python scripts/verify_spotify_access.py
"""
import base64
import os
import sys

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
WARN = "\033[33mWARN\033[0m"


def _basic_auth() -> str:
    return base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()


def check_token() -> str | None:
    if not CLIENT_ID or not CLIENT_SECRET:
        print(f"  {FAIL} SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET not set")
        return None
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {_basic_auth()}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials"},
        timeout=10,
    )
    if resp.status_code == 200:
        data = resp.json()
        print(f"  {PASS} Token obtained (expires_in={data.get('expires_in')}s)")
        return data["access_token"]
    print(f"  {FAIL} HTTP {resp.status_code} — {resp.text[:200]}")
    return None


def check_search(token: str) -> None:
    resp = requests.get(
        "https://api.spotify.com/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": "test", "type": "track", "limit": 1},
        timeout=10,
    )
    if resp.status_code == 200:
        items = resp.json().get("tracks", {}).get("items", [])
        print(f"  {PASS} Search OK — first result: {items[0]['name']!r if items else '(none)'}")
    else:
        print(f"  {FAIL} Search HTTP {resp.status_code}")


def check_artists(token: str) -> None:
    resp = requests.get(
        "https://api.spotify.com/v1/artists/06HL4z0CvFAxyc27GXpf02",  # Taylor Swift
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp.status_code == 200:
        genres = resp.json().get("genres", [])[:3]
        print(f"  {PASS} Artists OK — sample genres: {genres}")
    else:
        print(f"  {FAIL} Artists HTTP {resp.status_code}")


def check_audio_features(token: str) -> None:
    resp = requests.get(
        "https://api.spotify.com/v1/audio-features/11dFghVXANMlKmJXsNCbNl",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp.status_code == 403:
        print(f"  {PASS} Audio features returns 403 (deprecated — expected)")
    elif resp.status_code == 200:
        print(f"  {WARN} Audio features returned 200 (endpoint may be re-enabled for this tier)")
    else:
        print(f"  {WARN} Audio features HTTP {resp.status_code} (expected 403)")


def main() -> None:
    print("=== Spotify API Access Verification ===\n")
    print("1. Client Credentials token")
    token = check_token()
    if token is None:
        sys.exit(1)
    print("\n2. Track search (GET /v1/search)")
    check_search(token)
    print("\n3. Artist metadata (GET /v1/artists/{id})")
    check_artists(token)
    print("\n4. Audio features (GET /v1/audio-features/{id}) — deprecated, expect 403")
    check_audio_features(token)
    print("\n=== Done ===")
    print("If checks 1–3 passed, enrichment is ready.")
    print("Embeddings use artist name + track name + genres; audio features are not used.")


if __name__ == "__main__":
    main()
