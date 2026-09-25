from fastapi.testclient import TestClient
from test_core_flow import call, register

from app.main import app
from app.rag.service import build_chunk_records


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



def test_chinese_chunker_preserves_subjectless_dialogue_context():
    story = """沈青衡停在石门前，没有立刻伸手。
顾七问：“还进去吗？”
“进去。”
“里面可能有人。”
“我知道。”
顾七盯着他：“那你为什么还去？”
“钥匙在里面。”
雨声压过屋檐。
“确定？”
“确定。”"""
    records = build_chunk_records(story)
    dialogue = [record for record in records if record["chunk_kind"] == "dialogue"]
    assert dialogue
    assert any(
        "沈青衡停在石门前" in record["text"]
        and "顾七问" in record["text"]
        and "“进去。”" in record["text"]
        and "“钥匙在里面。”" in record["text"]
        for record in dialogue
    )


def test_chinese_dialogue_retrieval_returns_neighbor_context():
    client = TestClient(app)
    _, token = register(client, "rag-cn-dialogue")
    _, body = call(client, "POST", "/api/v1/workspaces", token, json={"name": "中文对白 RAG"})
    workspace = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", token, json={"name": "对白项目"})
    prefix = f"/api/v1/projects/{body['data']['id']}"
    story = """沈青衡停在石门前，没有立刻伸手。
顾七问：“还进去吗？”
“进去。”
“里面可能有人。”
“我知道。”
顾七盯着他：“那你为什么还去？”
“钥匙在里面。”
雨声压过屋檐。
“确定？”
“确定。”
两人随后一起推开石门。"""
    code, body = call(client, "POST", f"{prefix}/stories", token, json={"title": "石门对白", "content": story})
    assert code == 200, body
    story_id = body["data"]["id"]
    code, body = call(client, "POST", f"{prefix}/knowledge/stories/{story_id}/index", token)
    assert code == 200, body

    code, body = call(
        client,
        "POST",
        f"{prefix}/knowledge/search",
        token,
        json={"query": "顾七问为什么还要进去，对方怎么回答？", "limit": 5},
    )
    assert code == 200, body
    assert body["data"], body
    assert any(
        "顾七" in result["text"] and "钥匙在里面" in result["text"]
        for result in body["data"]
    )
    assert any(result.get("chunk_kind") == "dialogue" for result in body["data"])
