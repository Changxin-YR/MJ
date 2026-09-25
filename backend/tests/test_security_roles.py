from uuid import uuid4

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
    code, body = call(client, "POST", prefix + f"/pending-actions/{uuid4()}/approve", viewer)
    assert code == 403 and body["error"]["code"] == "PERMISSION_DENIED"
    code, body = call(client, "POST", prefix + "/members", editor, json={"email": viewer_email, "role": "OWNER"})
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


def test_workspace_member_cannot_access_another_project_in_same_workspace():
    client = TestClient(app)
    _, owner = register(client, "same-workspace-owner")
    member_email, member = register(client, "same-workspace-member")
    code, body = call(client, "POST", "/api/v1/workspaces", owner, json={"name": "Isolated Workspace"})
    assert code == 200, body
    workspace = body["data"]["id"]
    projects = []
    for name in ("Allowed", "Private"):
        code, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", owner, json={"name": name})
        assert code == 200, body
        projects.append(body["data"]["id"])
    code, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/members", owner, json={"email": member_email, "role": "MEMBER"})
    assert code == 200, body
    code, body = call(client, "POST", f"/api/v1/projects/{projects[0]}/members", owner, json={"email": member_email, "role": "EDITOR"})
    assert code == 200, body
    code, body = call(client, "GET", f"/api/v1/projects/{projects[0]}", member)
    assert code == 200, body
    code, body = call(client, "GET", f"/api/v1/projects/{projects[1]}", member)
    assert code == 404 and body["error"]["code"] == "RESOURCE_NOT_FOUND"
    code, body = call(client, "POST", f"/api/v1/projects/{projects[1]}/stories", member, json={"title": "stolen", "content": "private"})
    assert code == 404 and body["error"]["code"] == "RESOURCE_NOT_FOUND"
