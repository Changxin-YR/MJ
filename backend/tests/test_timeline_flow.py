from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from test_core_flow import call, register
from test_generation_flow import make_ready_shot

from app.db import SessionLocal
from app.main import app
from app.models import Shot
from app.workers.tasks import process_generation


def test_render_approved_timeline_to_playable_mp4():
    client = TestClient(app)
    _, token = register(client, "timeline")
    prefix, shot = make_ready_shot(client, token)
    sentinel = Path(f"/tmp/frameforge-ffmpeg-injection-{uuid4().hex}")
    code, body = call(client, "PATCH", f"{prefix}/shots/{shot['id']}", token, json={"expected_version": shot["version"], "dialogue": f"The city is waking up;$(touch {sentinel})"})
    assert code == 200, body
    shot = body["data"]
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
    with SessionLocal() as db:
        current = db.get(Shot, shot["id"])
        current.inspection_json = {
            "status": "FAIL",
            "issues": ["Japanese text appeared later in the video"],
            "checks": {"visible_text_language": "FAIL"},
            "review_override": {
                "actor_id": "legacy-reviewer",
                "reason": "legacy override must no longer bypass language policy",
                "video_asset_id": current.current_video_asset_id,
                "reviewed_at": "2026-09-25T00:00:00",
            },
        }
        db.commit()
    code, body = call(client, "POST", f"{prefix}/timelines/{timeline['id']}/render", token, json={"expected_version": timeline["version"]})
    assert code == 409 and body["error"]["code"] == "RESOURCE_CONFLICT"
    with SessionLocal() as db:
        current = db.get(Shot, shot["id"])
        current.inspection_json = {"status": "FAIL", "issues": ["Recheck found a mismatch"]}
        db.commit()
    code, body = call(client, "POST", f"{prefix}/timelines/{timeline['id']}/sync", token, json={"expected_version": timeline["version"]})
    assert code == 409 and body["error"]["code"] == "RESOURCE_CONFLICT"
    code, body = call(client, "POST", f"{prefix}/timelines/{timeline['id']}/render", token, json={"expected_version": timeline["version"]})
    assert code == 409 and body["error"]["code"] == "RESOURCE_CONFLICT"
    code, body = call(client, "GET", f"{prefix}/shots/{shot['id']}", token)
    assert code == 200, body
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/transition", token, json={"expected_version": body["data"]["version"], "target": "APPROVED", "review_reason": "I checked the current video frame by frame and the mismatch is not present."})
    assert code == 200 and body["data"]["status"] == "APPROVED"
    assert body["data"]["inspection_json"]["review_override"]["video_asset_id"] == shot["current_video_asset_id"]
    code, body = call(client, "POST", f"{prefix}/timelines/{timeline['id']}/render", token, json={"expected_version": timeline["version"]})
    assert code == 200, body
    final_asset_id = body["data"]["final_asset_id"]
    response = client.get(f"{prefix}/assets/{final_asset_id}/content", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200 and response.content[4:8] == b"ftyp"
    assert not sentinel.exists()
