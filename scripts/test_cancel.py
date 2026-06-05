#!/usr/bin/env python3
"""
Cancellation smoke-test for the Distributed Job Processing Platform.

Tests all four cancellation cases end-to-end against a live API.

Usage:
    python scripts/test_cancel.py

Requires the full stack to be running:
    docker compose up --build
"""

import sys
import time

import httpx

BASE_URL = "http://localhost:8000"
ETL_PAYLOAD = {"source": "raw", "destination": "clean"}
# 2 features forces a shape/missing-model error every attempt → exhausts retries quickly
ML_BAD_PAYLOAD = {"features": [0.5, 1.2]}

_PASS = 0
_FAIL = 0


def _report(label: str, passed: bool, detail: str = "") -> bool:
    global _PASS, _FAIL
    tag = "PASS" if passed else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"[{tag}] {label}{suffix}")
    if passed:
        _PASS += 1
    else:
        _FAIL += 1
    return passed


def _submit(client: httpx.Client, job_type: str, payload: dict) -> str:
    resp = client.post("/jobs", json={"job_type": job_type, "payload": payload})
    resp.raise_for_status()
    return resp.json()["job_id"]


def _status(client: httpx.Client, job_id: str) -> str:
    resp = client.get(f"/jobs/{job_id}")
    resp.raise_for_status()
    return resp.json()["status"]


def _cancel(client: httpx.Client, job_id: str) -> httpx.Response:
    return client.delete(f"/jobs/{job_id}")


def _poll_until(client: httpx.Client, job_id: str, target: set[str], timeout: int) -> str:
    """Return the first status in `target`, or whatever status exists at timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = _status(client, job_id)
        if st in target:
            return st
        time.sleep(0.5)
    return _status(client, job_id)


# ---------------------------------------------------------------------------
# Case A — cancel while still pending
# ---------------------------------------------------------------------------
def case_a(client: httpx.Client) -> None:
    """Submit and cancel before the worker has a chance to pick the job up."""
    job_id = _submit(client, "etl_pipeline", ETL_PAYLOAD)
    resp = _cancel(client, job_id)

    if resp.status_code != 200:
        _report("Case A — cancel pending", False, f"DELETE → {resp.status_code}")
        return

    st = _status(client, job_id)
    _report("Case A — cancel pending", st == "cancelled", f"status={st}")


# ---------------------------------------------------------------------------
# Case B — cancel while running
# ---------------------------------------------------------------------------
def case_b(client: httpx.Client) -> None:
    """Submit, wait for the worker to start executing, then cancel."""
    job_id = _submit(client, "etl_pipeline", ETL_PAYLOAD)

    # etl_pipeline sleeps 5-8 s; poll until running (up to 10 s)
    st = _poll_until(client, job_id, {"running", "complete", "failed", "cancelled"}, timeout=10)
    if st != "running":
        _report("Case B — cancel running", False, f"job never entered running (status={st})")
        return

    resp = _cancel(client, job_id)
    if resp.status_code != 200:
        _report("Case B — cancel running", False, f"DELETE → {resp.status_code}")
        return

    # Status is set to cancelled synchronously by the service layer
    st = _status(client, job_id)
    _report("Case B — cancel running", st == "cancelled", f"status={st}")


# ---------------------------------------------------------------------------
# Case C — cancel after completion (expect 409)
# ---------------------------------------------------------------------------
def case_c(client: httpx.Client) -> None:
    """Submit, wait for the job to complete, then attempt cancellation."""
    job_id = _submit(client, "etl_pipeline", ETL_PAYLOAD)

    # etl_pipeline takes 5-8 s; allow up to 30 s
    st = _poll_until(client, job_id, {"complete", "failed", "cancelled"}, timeout=30)
    if st != "complete":
        _report("Case C — cancel complete", False, f"job did not complete (status={st})")
        return

    resp = _cancel(client, job_id)
    _report(
        "Case C — cancel complete",
        resp.status_code == 409,
        f"DELETE → {resp.status_code}",
    )


# ---------------------------------------------------------------------------
# Case D — cancel after failure (expect 409)
# ---------------------------------------------------------------------------
def case_d(client: httpx.Client) -> None:
    """Submit an ml_inference job that will always fail (wrong feature count),
    wait for it to exhaust retries, then attempt cancellation."""
    job_id = _submit(client, "ml_inference", ML_BAD_PAYLOAD)

    # Retries fire at ~1 s, ~2 s, ~4 s after each attempt → ~7-9 s total
    # Allow up to 60 s so slow environments don't flake
    print(f"       (waiting for ml_inference to exhaust retries — up to 60 s …)")
    st = _poll_until(client, job_id, {"failed", "complete", "cancelled"}, timeout=60)
    if st != "failed":
        _report("Case D — cancel failed", False, f"job did not fail (status={st})")
        return

    resp = _cancel(client, job_id)
    _report(
        "Case D — cancel failed",
        resp.status_code == 409,
        f"DELETE → {resp.status_code}",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    print(f"Target: {BASE_URL}\n")

    with httpx.Client(base_url=BASE_URL, timeout=15.0) as client:
        try:
            client.get("/jobs?limit=1").raise_for_status()
        except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
            print(f"ERROR: Cannot reach {BASE_URL} — {exc}")
            print("Start the stack with:  docker compose up --build")
            sys.exit(1)

        case_a(client)
        case_b(client)
        case_c(client)
        case_d(client)

    total = _PASS + _FAIL
    print(f"\n{_PASS}/{total} passed")
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    main()
