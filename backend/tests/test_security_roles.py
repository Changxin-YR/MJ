from fastapi.testclient import TestClient
from test_core_flow import call, register
from test_generation_flow import make_ready_shot

from app.db import SessionLocal
from app.main import app
from app.models import Shot


def test_viewer_editor_and_cross_project_boundaries():
    client = TestClient(app)
    _, owner = register(client, "security-owner")
    viewer_email, viewer = register(client, "security-viewer")
    editor_email, editor = register(client, "security-editor")
    prefix, shot = make_ready_shot(client, owner)
    project_id = prefix.rsplit("/", 1)[1]
    code, body = call(client, "GET", prefix, owner)
    assert code == 200, body
    workspace_id = body["data"]["workspace_id"]
    for email, role in ((viewer_email, "VIEWER"), (editor_email, "EDITOR")):
        code, body = call(client, "POST", f"/api/v1/workspaces/{workspace_id}/members", owner, json={"email": email, "role": "MEMBER"})
        assert code == 200, body
        code, body = call(client, "POST", prefix + "/members", owner, json={"email": email, "role": role})
        assert code == 200, body

    code, body = call(client, "POST", prefix + f"/shots/{shot['id']}/generations", viewer, json={"kind": "IMAGE", "idempotency_key": "viewer-denied-1"})
    assert code == 403 and body["error"]["code"] == "PERMISSION_DENIED"

    with SessionLocal() as db:
        row = db.get(Shot, shot["id"])
        row.status = "LOCKED"
        db.commit()
    code, body = call(client, "PATCH", prefix + f"/shots/{shot['id']}", editor, json={"expected_version": shot["version"], "description": "unauthorized change"})
    assert code == 409 and body["error"]["code"] == "SHOT_LOCKED"

    _, stranger = register(client, "security-stranger")
    code, body = call(client, "GET", prefix + f"/shots/{shot['id']}", stranger)
    assert code == 404 and body["error"]["code"] == "RESOURCE_NOT_FOUND"
    code, body = call(client, "GET", f"/api/v1/projects/{project_id}", stranger)
    assert code == 404 and body["error"]["code"] == "RESOURCE_NOT_FOUND"
