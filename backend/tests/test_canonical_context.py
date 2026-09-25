from fastapi.testclient import TestClient
from test_core_flow import call, register

from app.agent.gateway import gateway
from app.main import app


def _project(client: TestClient, token: str, workspace_name: str, project_name: str):
    code, body = call(client, "POST", "/api/v1/workspaces", token, json={"name": workspace_name})
    assert code == 200, body
    workspace_id = body["data"]["id"]
    code, body = call(client, "POST", f"/api/v1/workspaces/{workspace_id}/projects", token, json={"name": project_name})
    assert code == 200, body
    return workspace_id, f"/api/v1/projects/{body['data']['id']}"


def test_project_bible_relationships_permissions_versions_and_director_context():
    client = TestClient(app)
    owner_email, owner = register(client, "canonical-owner")
    viewer_email, viewer = register(client, "canonical-viewer")
    workspace_id, prefix = _project(client, owner, "Canonical Studio", "Canonical Project")

    code, body = call(
        client,
        "POST",
        f"/api/v1/workspaces/{workspace_id}/members",
        owner,
        json={"email": viewer_email, "role": "MEMBER"},
    )
    assert code == 200, body
    code, body = call(client, "POST", prefix + "/members", owner, json={"email": viewer_email, "role": "VIEWER"})
    assert code == 200, body

    code, body = call(
        client,
        "POST",
        prefix + "/bibles",
        owner,
        json={"title": "灵气规则", "content": "引气入体前不能主动驱使灵气；这是项目不可变规则。"},
    )
    assert code == 200, body
    bible = body["data"]
    code, body = call(client, "GET", prefix + "/bibles", viewer)
    assert code == 200 and body["data"][0]["title"] == "灵气规则"
    code, body = call(
        client,
        "PATCH",
        prefix + f"/bibles/{bible['id']}",
        owner,
        json={"expected_version": bible["version"], "content": "引气入体前不能主动驱使灵气；正式规则。"},
    )
    assert code == 200, body
    bible = body["data"]
    code, body = call(
        client,
        "PATCH",
        prefix + f"/bibles/{bible['id']}",
        owner,
        json={"expected_version": 1, "content": "旧版本覆盖"},
    )
    assert code == 409 and body["error"]["code"] == "RESOURCE_VERSION_CONFLICT"
    code, body = call(
        client,
        "POST",
        prefix + "/bibles",
        viewer,
        json={"title": "越权", "content": "Viewer 不应写入"},
    )
    assert code == 403 and body["error"]["code"] == "PERMISSION_DENIED"

    characters = []
    for name in ("沈青衡", "顾七"):
        code, body = call(client, "POST", prefix + "/characters", owner, json={"name": name})
        assert code == 200, body
        characters.append(body["data"])

    code, body = call(
        client,
        "POST",
        prefix + "/character-relationships",
        owner,
        json={
            "source_character_id": characters[1]["id"],
            "target_character_id": characters[0]["id"],
            "description": "顾七是沈青衡在杂役院的同伴，彼此信任，但对风险判断不同。",
        },
    )
    assert code == 200, body
    relationship = body["data"]
    assert relationship["source_character_name"] == "顾七"
    assert relationship["target_character_name"] == "沈青衡"

    code, body = call(
        client,
        "POST",
        prefix + "/character-relationships",
        owner,
        json={
            "source_character_id": characters[1]["id"],
            "target_character_id": characters[0]["id"],
            "description": "重复关系",
        },
    )
    assert code == 409 and body["error"]["code"] == "RESOURCE_CONFLICT"

    code, body = call(client, "GET", prefix + "/character-relationships", viewer)
    assert code == 200 and len(body["data"]) == 1
    code, body = call(
        client,
        "PATCH",
        prefix + f"/character-relationships/{relationship['id']}",
        viewer,
        json={"expected_version": relationship["version"], "description": "Viewer 越权修改"},
    )
    assert code == 403 and body["error"]["code"] == "PERMISSION_DENIED"

    _, other_prefix = _project(client, owner, "Other Studio", "Other Project")
    code, body = call(client, "POST", other_prefix + "/characters", owner, json={"name": "外部角色"})
    assert code == 200, body
    foreign_character = body["data"]
    code, body = call(
        client,
        "POST",
        prefix + "/character-relationships",
        owner,
        json={
            "source_character_id": characters[0]["id"],
            "target_character_id": foreign_character["id"],
            "description": "跨项目关系必须拒绝",
        },
    )
    assert code == 404 and body["error"]["code"] == "RESOURCE_NOT_FOUND"

    code, body = call(
        client,
        "POST",
        prefix + "/director/runs",
        owner,
        json={"request": "分析项目规则和角色关系", "intent": "ANALYZE"},
    )
    assert code == 200, body
    run = body["data"]
    context = gateway.invoke(run["id"], "load_project_context", {})
    assert any(item["title"] == "灵气规则" and "正式规则" in item["content"] for item in context["bibles"])
    assert any(
        item["source_character_name"] == "顾七"
        and item["target_character_name"] == "沈青衡"
        and "风险判断不同" in item["description"]
        for item in context["relationships"]
    )

    code, body = call(
        client,
        "DELETE",
        prefix + f"/character-relationships/{relationship['id']}",
        owner,
        json={"expected_version": relationship["version"]},
    )
    assert code == 200 and body["data"]["deleted"] is True
    code, body = call(
        client,
        "DELETE",
        prefix + f"/bibles/{bible['id']}",
        owner,
        json={"expected_version": bible["version"]},
    )
    assert code == 200 and body["data"]["deleted"] is True
