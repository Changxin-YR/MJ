from uuid import uuid4

from fastapi.testclient import TestClient
from test_core_flow import call, register

from app.main import app
from app.workers.dispatcher import dispatch_batch
from app.workers.tasks import process_generation


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
