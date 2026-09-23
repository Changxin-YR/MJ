from fastapi.testclient import TestClient
from test_core_flow import call, register

from app.main import app


def test_cross_project_retrieval_returns_zero_other_chunks():
    client = TestClient(app)
    _, token = register(client, "rag")
    _, body = call(client, "POST", "/api/v1/workspaces", token, json={"name": "RAG Scope"})
    workspace = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", token, json={"name": "A"})
    project_a = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", token, json={"name": "B"})
    project_b = body["data"]["id"]
    a = f"/api/v1/projects/{project_a}"
    b = f"/api/v1/projects/{project_b}"
    _, body = call(client, "POST", f"{b}/stories", token, json={"title": "Secret", "content": "The azure cryptogram is hidden under a silver observatory."})
    secret_story = body["data"]["id"]
    code, body = call(client, "POST", f"{b}/knowledge/stories/{secret_story}/index", token)
    assert code == 200, body
    code, body = call(client, "POST", f"{a}/knowledge/search", token, json={"query": "azure cryptogram"})
    assert code == 200, body
    assert body["data"] == []
    code, body = call(client, "POST", f"{b}/knowledge/search", token, json={"query": "azure cryptogram"})
    assert code == 200 and len(body["data"]) >= 1, body
