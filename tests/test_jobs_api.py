import uuid
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_submit_ml_inference_job(client):
    resp = await client.post("/jobs", json={"job_type": "ml_inference", "payload": {"features": [0.5, 1.2, 3.4]}})
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "pending"
    assert "job_id" in data


@pytest.mark.asyncio
async def test_submit_etl_pipeline_job(client):
    resp = await client.post("/jobs", json={"job_type": "etl_pipeline", "payload": {"source": "raw", "destination": "clean"}})
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_submit_report_generation_job(client):
    resp = await client.post("/jobs", json={"job_type": "report_generation", "payload": {"report_type": "monthly_summary", "date_range": {"start": "2026-01-01", "end": "2026-01-31"}}})
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_get_job(client):
    submit = await client.post("/jobs", json={"job_type": "etl_pipeline", "payload": {"source": "a", "destination": "b"}})
    job_id = submit.json()["job_id"]

    resp = await client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_get_job_not_found(client):
    resp = await client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_jobs(client):
    await client.post("/jobs", json={"job_type": "etl_pipeline", "payload": {"source": "a", "destination": "b"}})
    await client.post("/jobs", json={"job_type": "report_generation", "payload": {"report_type": "monthly_summary", "date_range": {"start": "2026-01-01", "end": "2026-01-31"}}})

    resp = await client.get("/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["jobs"]) == 2


@pytest.mark.asyncio
async def test_submit_invalid_job_type(client):
    resp = await client.post("/jobs", json={"job_type": "unknown_type", "payload": {}})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_idempotency_key_returns_200_on_duplicate(client):
    payload = {"job_type": "etl_pipeline", "payload": {"source": "a", "destination": "b"}, "idempotency_key": "key-abc"}
    first = await client.post("/jobs", json=payload)
    assert first.status_code == 202
    first_id = first.json()["job_id"]

    second = await client.post("/jobs", json=payload)
    assert second.status_code == 200
    assert second.json()["job_id"] == first_id


@pytest.mark.asyncio
async def test_idempotency_key_different_keys_create_separate_jobs(client):
    body = {"job_type": "etl_pipeline", "payload": {"source": "a", "destination": "b"}}
    r1 = await client.post("/jobs", json={**body, "idempotency_key": "key-1"})
    r2 = await client.post("/jobs", json={**body, "idempotency_key": "key-2"})
    assert r1.json()["job_id"] != r2.json()["job_id"]


@pytest.mark.asyncio
async def test_cancel_pending_job(client):
    submit = await client.post("/jobs", json={"job_type": "etl_pipeline", "payload": {"source": "a", "destination": "b"}})
    job_id = submit.json()["job_id"]

    with patch("worker.celery_app.celery_app") as mock_celery:
        resp = await client.delete(f"/jobs/{job_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"
    assert data["job_id"] == job_id


@pytest.mark.asyncio
async def test_cancel_nonexistent_job(client):
    with patch("worker.celery_app.celery_app"):
        resp = await client.delete(f"/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_already_cancelled_job_returns_409(client):
    submit = await client.post("/jobs", json={"job_type": "etl_pipeline", "payload": {"source": "a", "destination": "b"}})
    job_id = submit.json()["job_id"]

    with patch("worker.celery_app.celery_app"):
        await client.delete(f"/jobs/{job_id}")
        resp = await client.delete(f"/jobs/{job_id}")

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_dead_letter_empty(client):
    resp = await client.get("/jobs/dead-letter")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["jobs"] == []
