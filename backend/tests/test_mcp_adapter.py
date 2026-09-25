import pytest
from fastapi.testclient import TestClient
from test_core_flow import call, register

from app.api.errors import APIError
from app.main import app
from app.mcp.server import invoke_bound


def create_analyze_run(client, token, name):
    _, body = call(client, "POST", "/api/v1/workspaces", token, json={"name": name})
    workspace_id = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace_id}/projects", token, json={"name": name})
    project_id = body["data"]["id"]
    code, body = call(client, "POST", f"/api/v1/projects/{project_id}/director/runs", token, json={"request": "Summarize the project", "intent": "ANALYZE"})
    assert code == 200, body
    return body["data"]["id"]


def test_mcp_tools_bind_run_to_live_user_session(monkeypatch):
    client = TestClient(app)
    _, token_a = register(client, "mcp-a")
    run_a = create_analyze_run(client, token_a, "Project A")
    _, token_b = register(client, "mcp-b")
    run_b = create_analyze_run(client, token_b, "Project B")

    monkeypatch.setenv("FRAMEFORGE_MCP_ACCESS_TOKEN", token_a)
    assert invoke_bound(run_a, "load_project_context", {})["project"]["name"] == "Project A"
    with pytest.raises(APIError) as error:
        invoke_bound(run_b, "load_project_context", {})
    assert error.value.code == "PERMISSION_DENIED"

    code, body = call(client, "POST", "/api/v1/auth/logout", token_a)
    assert code == 200, body
    with pytest.raises(APIError) as error:
        invoke_bound(run_a, "load_project_context", {})
    assert error.value.code == "PERMISSION_DENIED"
