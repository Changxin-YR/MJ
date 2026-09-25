from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def call(client, method, path, token=None, **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.request(method, path, headers=headers, **kwargs)
    return response.status_code, response.json()


def register(client, name):
    email = f"{name}-{uuid4().hex[:8]}@example.com"
    code, body = call(client, "POST", "/api/v1/auth/register", json={"email": email, "password": "StrongPassword123!", "display_name": name})
    assert code == 200, body
    return email, body["data"]["access_token"]


def test_content_scope_versions_and_locked_shot():
    client = TestClient(app)
    _, owner = register(client, "owner")
    code, body = call(client, "POST", "/api/v1/workspaces", owner, json={"name": "Studio"})
    assert code == 200, body
    workspace = body["data"]["id"]
    code, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", owner, json={"name": "Story Project"})
    assert code == 200, body
    project = body["data"]["id"]
    prefix = f"/api/v1/projects/{project}"

    code, body = call(client, "POST", f"{prefix}/stories", owner, json={"title": "Opening", "content": "A courier finds a letter that can change the fate of the whole city."})
    assert code == 200, body
    code, body = call(client, "POST", f"{prefix}/characters", owner, json={"name": "Mira", "dna": {"face": "freckles", "prompt_anchor": "red scarf"}})
    assert code == 200, body
    character = body["data"]
    version = character["versions"][0]["id"]
    code, body = call(client, "POST", f"{prefix}/characters/{character['id']}/versions/{version}/activate", owner, json={"expected_version": 1})
    assert code == 200, body

    code, body = call(client, "POST", f"{prefix}/episodes", owner, json={"title": "The Letter"})
    assert code == 200, body
    episode = body["data"]["id"]
    code, body = call(client, "POST", f"{prefix}/episodes/{episode}/scenes", owner, json={"heading": "EXT. CITY ROOFTOP"})
    assert code == 200, body
    scene = body["data"]["id"]
    code, body = call(client, "POST", f"{prefix}/scenes/{scene}/shots", owner, json={"description": "Mira looks at the city", "character_ids": [character["id"]]})
    assert code == 200, body
    shot = body["data"]
    code, body = call(client, "GET", f"{prefix}/shots/{shot['id']}/built-prompt", owner)
    assert code == 200 and "red scarf" in body["data"]["prompt"]

    code, body = call(client, "PATCH", f"{prefix}/shots/{shot['id']}", owner, json={"expected_version": 99, "description": "wrong"})
    assert code == 409 and body["error"]["code"] == "RESOURCE_VERSION_CONFLICT"
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/transition", owner, json={"expected_version": 1, "target": "LOCKED"})
    assert code == 409 and body["error"]["code"] == "RESOURCE_CONFLICT"

    _, stranger = register(client, "stranger")
    code, body = call(client, "GET", f"{prefix}/stories", stranger)
    assert code == 404 and body["error"]["code"] == "RESOURCE_NOT_FOUND"
    code, body = call(client, "GET", f"{prefix}/shots/{shot['id']}", stranger)
    assert code == 404 and body["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_refresh_rotation_reuse_revokes_session():
    client = TestClient(app)
    _, token = register(client, "rotation")
    original = client.cookies.get("refresh_token")
    code, body = call(client, "POST", "/api/v1/auth/refresh")
    assert code == 200, body
    assert client.cookies.get("refresh_token") != original
    client.cookies.set("refresh_token", original)
    code, body = call(client, "POST", "/api/v1/auth/refresh")
    assert code == 401 and body["error"]["code"] == "AUTH_REQUIRED"
    code, body = call(client, "GET", "/api/v1/auth/me", token)
    assert code == 401



def test_parallel_episode_creation_and_timeline_creation_are_serialized():
    client = TestClient(app)
    _, owner = register(client, "parallel-content")
    code, body = call(client, "POST", "/api/v1/workspaces", owner, json={"name": "Parallel Studio"})
    assert code == 200, body
    workspace = body["data"]["id"]
    code, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", owner, json={"name": "Parallel Project"})
    assert code == 200, body
    prefix = f"/api/v1/projects/{body['data']['id']}"

    barrier = Barrier(2)

    def create_episode(title):
        with TestClient(app) as request_client:
            barrier.wait()
            return call(request_client, "POST", f"{prefix}/episodes", owner, json={"title": title})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create_episode, ["并发第一集", "并发第二集"]))
    assert [code for code, _ in results] == [200, 200], results
    episode_numbers = {body["data"]["episode_no"] for _, body in results}
    assert episode_numbers == {1, 2}

    episode_id = results[0][1]["data"]["id"]
    barrier = Barrier(2)

    def create_timeline(_):
        with TestClient(app) as request_client:
            barrier.wait()
            return call(request_client, "POST", f"{prefix}/episodes/{episode_id}/timeline", owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        timeline_results = list(pool.map(create_timeline, range(2)))
    assert [code for code, _ in timeline_results] == [200, 200], timeline_results
    assert len({body["data"]["id"] for _, body in timeline_results}) == 1
