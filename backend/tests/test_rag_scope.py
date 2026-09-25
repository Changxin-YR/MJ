import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_core_flow import call, register

from app.db import SessionLocal
from app.main import app
from app.models import KnowledgeDocument, StorySource
from app.providers.embeddings import FakeEmbeddingProvider
from app.rag import service as rag_service
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



def test_reindex_same_story_keeps_one_knowledge_document():
    client = TestClient(app)
    _, token = register(client, "rag-reindex")
    _, body = call(client, "POST", "/api/v1/workspaces", token, json={"name": "RAG Reindex"})
    workspace = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", token, json={"name": "RAG Reindex Project"})
    project_id = body["data"]["id"]
    prefix = f"/api/v1/projects/{project_id}"
    text = "顾七问：“还去吗？”\n“去。”\n“为什么？”\n“钥匙在里面。”"
    _, body = call(client, "POST", f"{prefix}/stories", token, json={"title": "重复索引", "content": text})
    story_id = body["data"]["id"]

    for _ in range(2):
        code, body = call(client, "POST", f"{prefix}/knowledge/stories/{story_id}/index", token)
        assert code == 200, body

    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(
                KnowledgeDocument.project_id == project_id,
                KnowledgeDocument.source_type == "story",
                KnowledgeDocument.source_id == story_id,
            )
        )
        assert count == 1


class _FailingEmbedding(FakeEmbeddingProvider):
    def embed(self, text: str) -> list[float]:
        raise RuntimeError("simulated embedding outage")


def test_failed_reindex_preserves_previous_qdrant_points(monkeypatch):
    client = TestClient(app)
    _, token = register(client, "rag-reindex-failure")
    _, body = call(client, "POST", "/api/v1/workspaces", token, json={"name": "RAG Safe Replace"})
    workspace = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", token, json={"name": "RAG Safe Replace Project"})
    project_id = body["data"]["id"]
    prefix = f"/api/v1/projects/{project_id}"
    _, body = call(
        client,
        "POST",
        f"{prefix}/stories",
        token,
        json={"title": "安全重建", "content": "沈青衡把青铜钥匙藏在石门后的第三块砖下。顾七亲眼看见了。"},
    )
    story_id = body["data"]["id"]
    code, body = call(client, "POST", f"{prefix}/knowledge/stories/{story_id}/index", token)
    assert code == 200, body

    before = rag_service.retrieve(
        workspace_id=workspace,
        project_id=project_id,
        query="青铜钥匙藏在哪里",
        limit=5,
    )
    assert before and any("第三块砖" in item["text"] for item in before)

    with SessionLocal() as db:
        story = db.scalar(select(StorySource).where(StorySource.id == story_id))
        assert story
        monkeypatch.setattr(rag_service, "selected_embedding_provider", lambda: _FailingEmbedding())
        with pytest.raises(RuntimeError, match="simulated embedding outage"):
            rag_service.index_story(db, story)

    monkeypatch.setattr(rag_service, "selected_embedding_provider", lambda: FakeEmbeddingProvider())
    after = rag_service.retrieve(
        workspace_id=workspace,
        project_id=project_id,
        query="青铜钥匙藏在哪里",
        limit=5,
    )
    assert after and any("第三块砖" in item["text"] for item in after)



def _long_subjectless_dialogue_story() -> str:
    return """沈青衡和顾七沿着地宫石阶往下走，前面只剩一盏将灭的青灯。
顾七低声问：“还要继续往里走吗？”
“走。”
“前面已经没有地图了。”
“我知道。”
“刚才那道门差点把我们困死。”
“所以这次慢一点。”
“你听见水声了吗？”
“听见了，在左边。”
“可左边是封死的墙。”
“墙后是空的。”
“你怎么知道？”
“风从缝里出来。”
雨水从他们衣角滴到石阶上。
“那盏青灯要灭了。”
“灭了就别回头。”
“你总这么说。”
“因为回头更危险。”
“前面还有第二道门。”
“看见了。”
“门上没有锁孔。”
“锁不在门上。”
“那在哪？”
“石像手里。”
远处传来一声极轻的金属碰撞。
“你听见了吗？”
“赤铜铃。”
“那东西不是早就丢了吗？”
“没有。”
“那为什么还要进第二道门？”
“因为赤铜铃就在第二道门后，它能证明昨晚来过这里的人是谁。”
“确定？”
“确定。先别碰那盏灯。”
两人停在第二道门前，没有立刻伸手。"""


def test_long_subjectless_dialogue_keeps_anchor_and_speaker_hints():
    records = build_chunk_records(_long_subjectless_dialogue_story())
    dialogue = [record for record in records if record["chunk_kind"] == "dialogue"]
    assert len(dialogue) >= 2
    late = [record for record in dialogue if "赤铜铃就在第二道门后" in record["core_text"]]
    assert late
    assert any("顾七低声问" in record["text"] for record in late)
    assert any("顾七" in record.get("speaker_hints", []) for record in late)
    assert any(record.get("has_subjectless_dialogue") for record in late)


def test_long_dialogue_retrieval_expands_adjacent_context_across_chunk_boundary():
    client = TestClient(app)
    _, token = register(client, "rag-cn-long-dialogue")
    _, body = call(client, "POST", "/api/v1/workspaces", token, json={"name": "长对白 RAG"})
    workspace = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", token, json={"name": "长对白项目"})
    prefix = f"/api/v1/projects/{body['data']['id']}"
    code, body = call(client, "POST", f"{prefix}/stories", token, json={"title": "地宫连续对白", "content": _long_subjectless_dialogue_story()})
    assert code == 200, body
    story_id = body["data"]["id"]
    code, body = call(client, "POST", f"{prefix}/knowledge/stories/{story_id}/index", token)
    assert code == 200, body

    code, body = call(
        client,
        "POST",
        f"{prefix}/knowledge/search",
        token,
        json={"query": "赤铜铃之后，顾七问为什么还要进第二道门，对方怎么回答？", "limit": 3},
    )
    assert code == 200, body
    assert body["data"], body
    best = body["data"][0]
    assert "顾七低声问" in best["text"]
    assert "为什么还要进第二道门" in best["text"]
    assert "赤铜铃就在第二道门后" in best["text"]
    assert best["chunk_kind"] == "dialogue"
    assert "顾七" in best.get("speaker_hints", [])
    assert best.get("context_expanded") is True
