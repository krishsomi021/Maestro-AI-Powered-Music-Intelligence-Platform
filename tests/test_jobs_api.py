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
