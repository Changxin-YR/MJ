import hashlib
import hmac
import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_core_flow import register
from test_generation_flow import make_ready_shot

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import GenerationJob, OutboxEvent, ProviderCallbackReceipt, Shot

SECRET = "callback-test-secret-at-least-32-bytes-long"


def signed_request(payload: dict, *, nonce: str | None = None, timestamp: int | None = None, provider: str = "dashscope"):
    body = json.dumps(payload, separators=(",", ":")).encode()
    nonce = nonce or uuid4().hex
    timestamp = timestamp or int(time.time())
    signed = provider.encode() + b"\n" + str(timestamp).encode() + b"\n" + nonce.encode() + b"\n" + body
    signature = hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()
    return body, {
        "X-FrameForge-Timestamp": str(timestamp),
        "X-FrameForge-Nonce": nonce,
        "X-FrameForge-Signature": signature,
        "Content-Type": "application/json",
    }


def running_job() -> str:
    client = TestClient(app)
    _, token = register(client, "callback")
    _, shot = make_ready_shot(client, token)
    with SessionLocal() as db:
        row = db.get(Shot, shot["id"])
        job = GenerationJob(
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            provider="dashscope",
            model="wan2.6-i2v-flash",
            resource_type="shot",
            resource_id=row.id,
            kind="VIDEO",
            input_json={},
            idempotency_key=uuid4().hex,
            remote_job_id="remote-video-123",
            status="RUNNING",
            trace_id=str(uuid4()),
        )
        db.add(job)
        db.commit()
        return job.id


def test_provider_callback_validates_signature_binding_state_and_replay(monkeypatch):
    monkeypatch.setattr(settings, "provider_callback_secret", SECRET)
    client = TestClient(app)
    job_id = running_job()
    payload = {"provider_job_id": job_id, "remote_job_id": "remote-video-123", "status": "SUCCEEDED"}
    endpoint = "/api/v1/provider-callbacks/dashscope"

    body, headers = signed_request(payload)
    response = client.post(endpoint, content=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"accepted": True, "job_id": job_id}
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        assert job.status == "RUNNING"  # The worker must fetch and verify the actual media.
        assert job.callback_received_at is not None
        assert db.scalar(select(func.count()).select_from(ProviderCallbackReceipt).where(ProviderCallbackReceipt.generation_job_id == job_id)) == 1
        assert db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.aggregate_id == job_id, OutboxEvent.topic == "generation.dispatch")) == 1

    response = client.post(endpoint, content=body, headers=headers)
    assert response.status_code == 409 and response.json()["error"]["code"] == "CALLBACK_REPLAY"

    response = client.post(endpoint, content=body + b" ", headers=headers)
    assert response.status_code == 401 and response.json()["error"]["code"] == "CALLBACK_SIGNATURE_INVALID"

    stale_body, stale_headers = signed_request(payload, timestamp=int(time.time()) - 301)
    response = client.post(endpoint, content=stale_body, headers=stale_headers)
    assert response.status_code == 401 and response.json()["error"]["code"] == "CALLBACK_EXPIRED"

    huge_time_body, huge_time_headers = signed_request(payload)
    huge_time_headers["X-FrameForge-Timestamp"] = "9" * 5000
    response = client.post(endpoint, content=huge_time_body, headers=huge_time_headers)
    assert response.status_code == 401 and response.json()["error"]["code"] == "CALLBACK_SIGNATURE_INVALID"

    response = client.post(endpoint, content=b"x" * (16 * 1024 + 1), headers=headers)
    assert response.status_code == 413 and response.json()["error"]["code"] == "INVALID_PARAMETER"

    wrong_remote = {**payload, "remote_job_id": "some-other-remote-task"}
    wrong_body, wrong_headers = signed_request(wrong_remote)
    response = client.post(endpoint, content=wrong_body, headers=wrong_headers)
    assert response.status_code == 409 and response.json()["error"]["code"] == "CALLBACK_JOB_MISMATCH"

    wrong_job = {**payload, "provider_job_id": str(uuid4())}
    wrong_body, wrong_headers = signed_request(wrong_job)
    response = client.post(endpoint, content=wrong_body, headers=wrong_headers)
    assert response.status_code == 404 and response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        job.status = "SUCCEEDED"
        db.commit()
    new_body, new_headers = signed_request(payload)
    response = client.post(endpoint, content=new_body, headers=new_headers)
    assert response.status_code == 409 and response.json()["error"]["code"] == "CALLBACK_JOB_MISMATCH"


def test_provider_callback_requires_configured_secret(monkeypatch):
    monkeypatch.setattr(settings, "provider_callback_secret", "")
    payload = {"provider_job_id": str(uuid4()), "remote_job_id": "remote", "status": "FAILED"}
    body, headers = signed_request(payload)
    response = TestClient(app).post("/api/v1/provider-callbacks/dashscope", content=body, headers=headers)
    assert response.status_code == 503 and response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
