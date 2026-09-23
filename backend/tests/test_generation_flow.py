from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_core_flow import call, register

from app.db import SessionLocal
from app.main import app
from app.models import GenerationJob, OutboxEvent, Shot
from app.providers.registry import registry
from app.workers.dispatcher import dispatch_batch
from app.workers.tasks import claim_job, fail_job, process_generation


def make_ready_shot(client, token):
    _, body = call(client, "POST", "/api/v1/workspaces", token, json={"name": "Generation Studio"})
    workspace = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", token, json={"name": "Generation"})
    project = body["data"]["id"]
    prefix = f"/api/v1/projects/{project}"
    _, body = call(client, "POST", f"{prefix}/episodes", token, json={"title": "Pilot"})
    episode = body["data"]["id"]
    _, body = call(client, "POST", f"{prefix}/episodes/{episode}/scenes", token, json={"heading": "EXT. ROOFTOP"})
    scene = body["data"]["id"]
    _, body = call(client, "POST", f"{prefix}/scenes/{scene}/shots", token, json={"description": "A courier runs across the rooftop", "dialogue": "The city is waking up.", "duration": 2})
    shot = body["data"]
    for target in ("PLANNED", "STORYBOARD_READY"):
        code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/transition", token, json={"expected_version": shot["version"], "target": target})
        assert code == 200, body
        shot = body["data"]
    return prefix, shot


def test_job_outbox_idempotency_and_fake_image():
    client = TestClient(app)
    _, token = register(client, "producer")
    prefix, shot = make_ready_shot(client, token)
    key = str(uuid4())
    request = {"kind": "IMAGE", "idempotency_key": key}
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/generations", token, json=request)
    assert code == 200, body
    job = body["data"]
    assert job["status"] == "QUEUED"
    code, duplicate = call(client, "POST", f"{prefix}/shots/{shot['id']}/generations", token, json=request)
    assert code == 200 and duplicate["data"]["id"] == job["id"]

    def fail_broker(_event):
        raise ConnectionError("temporary broker outage")

    assert dispatch_batch(publisher=fail_broker) == 0
    code, body = call(client, "GET", f"{prefix}/generation-jobs/{job['id']}", token)
    assert code == 200 and body["data"]["status"] == "QUEUED"
    process_generation(job["id"])
    process_generation(job["id"])
    code, body = call(client, "GET", f"{prefix}/generation-jobs/{job['id']}", token)
    assert code == 200 and body["data"]["status"] == "SUCCEEDED", body
    code, body = call(client, "GET", f"{prefix}/shots/{shot['id']}", token)
    assert code == 200 and body["data"]["status"] == "REVIEW_REQUIRED"
    assert body["data"]["current_image_asset_id"]
    response = client.get(f"{prefix}/assets/{body['data']['current_image_asset_id']}/content", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG")


def test_worker_retries_transient_provider_failure_then_stops_on_permanent_failure():
    client = TestClient(app)
    _, token = register(client, "provider-fault")
    _, shot = make_ready_shot(client, token)
    with SessionLocal() as db:
        row = db.get(Shot, shot["id"])
        job = GenerationJob(
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            provider="fake",
            model="fake-image",
            resource_type="shot",
            resource_id=row.id,
            kind="IMAGE",
            input_json={},
            idempotency_key=uuid4().hex,
            status="QUEUED",
            trace_id=str(uuid4()),
        )
        db.add(job)
        db.commit()
        job_id = job.id

    owner = claim_job(job_id)
    assert owner
    fail_job(job_id, owner, TimeoutError("temporary provider timeout"))
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        assert job.status == "RETRYING" and job.retry_count == 1
        assert db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job_id, OutboxEvent.deduplication_key == f"generation:{job_id}:1"))

    owner = claim_job(job_id)
    assert owner
    fail_job(job_id, owner, ValueError("permanent provider response"))
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        assert job.status == "FAILED" and job.finished_at is not None
        assert job.error_code == "GENERATION_FAILED"
    registry.success("fake")
