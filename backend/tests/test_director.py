from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_core_flow import call, register
from test_generation_flow import make_ready_shot

from app.agent.gateway import gateway
from app.api.errors import APIError
from app.main import app


def test_director_pending_approval_and_scoped_tool():
    client = TestClient(app)
    _, owner = register(client, "director")
    prefix, shot = make_ready_shot(client, owner)
    code, body = call(client, "POST", f"{prefix}/director/runs", owner, json={"request": "Generate an image for this shot", "intent": "GENERATE_IMAGE", "shot_id": shot["id"]})
    assert code == 200, body
    run = body["data"]
    assert run["status"] == "WAITING_APPROVAL"
    pending = run["pending_actions"][0]
    assert pending["status"] == "PENDING"
    with pytest.raises(APIError) as error:
        gateway.invoke(run["id"], "execute_sql", {})
    assert error.value.code == "PERMISSION_DENIED"
    with pytest.raises(APIError) as error:
        gateway.invoke(run["id"], "generate_shot", {"shot_id": shot["id"], "kind": "IMAGE", "idempotency_key": str(uuid4()), "project_id": "foreign"}, pending["id"])
    assert error.value.code == "PERMISSION_DENIED"
    code, body = call(client, "POST", f"{prefix}/pending-actions/{pending['id']}/approve", owner)
    assert code == 200, body
    code, body = call(client, "POST", f"/api/v1/director/runs/{run['id']}/resume", owner)
    assert code == 200, body
    assert body["data"]["status"] == "COMPLETED"
    assert body["data"]["pending_actions"][0]["status"] == "EXECUTED"
    assert body["data"]["generation_jobs"][0]["status"] == "QUEUED"


def test_revoked_session_cannot_resume_approved_action():
    client = TestClient(app)
    _, owner = register(client, "revoked")
    prefix, shot = make_ready_shot(client, owner)
    _, body = call(client, "POST", f"{prefix}/director/runs", owner, json={"request": "Generate this image", "intent": "GENERATE_IMAGE", "shot_id": shot["id"]})
    run = body["data"]
    action_id = run["pending_actions"][0]["id"]
    code, body = call(client, "POST", f"{prefix}/pending-actions/{action_id}/approve", owner)
    assert code == 200, body
    code, body = call(client, "POST", "/api/v1/auth/logout", owner)
    assert code == 200, body
    code, body = call(client, "POST", f"/api/v1/director/runs/{run['id']}/resume", owner)
    assert code == 401 and body["error"]["code"] == "AUTH_REQUIRED"
