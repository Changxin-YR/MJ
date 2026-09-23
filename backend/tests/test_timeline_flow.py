from uuid import uuid4

from fastapi.testclient import TestClient
from test_core_flow import call, register
from test_generation_flow import make_ready_shot

from app.main import app
from app.workers.tasks import process_generation


def test_render_approved_timeline_to_playable_mp4():
    client = TestClient(app)
    _, token = register(client, "timeline")
    prefix, shot = make_ready_shot(client, token)
    for kind in ("IMAGE", "VIDEO", "VOICE"):
        code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/generations", token, json={"kind": kind, "idempotency_key": str(uuid4())})
        assert code == 200, body
        process_generation(body["data"]["id"])
        code, body = call(client, "GET", f"{prefix}/shots/{shot['id']}", token)
        assert code == 200 and body["data"]["status"] == "REVIEW_REQUIRED", body
        shot = body["data"]
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/transition", token, json={"expected_version": shot["version"], "target": "APPROVED"})
    assert code == 200, body
    episode = shot["episode_id"]
    code, body = call(client, "POST", f"{prefix}/episodes/{episode}/timeline", token)
    assert code == 200, body
    timeline = body["data"]
    code, body = call(client, "POST", f"{prefix}/timelines/{timeline['id']}/sync", token, json={"expected_version": timeline["version"]})
    assert code == 200, body
    timeline = body["data"]
    assert len(timeline["items"]) == 3
    code, body = call(client, "POST", f"{prefix}/timelines/{timeline['id']}/render", token, json={"expected_version": timeline["version"]})
    assert code == 200, body
    final_asset_id = body["data"]["final_asset_id"]
    response = client.get(f"{prefix}/assets/{final_asset_id}/content", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200 and response.content[4:8] == b"ftyp"
