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



class _FlatEmbedding(FakeEmbeddingProvider):
    model = "flat-cn-dialogue-v1"
    dimensions = 32
    batch_size = 100

    def embed(self, text: str, text_type: str = "document") -> list[float]:
        return [1.0] + [0.0] * (self.dimensions - 1)

    def embed_many(self, texts: list[str], text_type: str = "document") -> list[list[float]]:
        return [self.embed(text, text_type=text_type) for text in texts]


def test_long_subjectless_dialogue_keeps_explicit_speaker_anchors():
    lines = [
        "顾七问：“你确定要进去？”",
        "沈青衡道：“确定。”",
    ]
    for index in range(18):
        lines.append(f"“第{index + 1}步照旧。”")
        lines.append(f"“知道了，继续。”")
    lines.extend(
        [
            "“钥匙不在门上。”",
            "“那在哪里？”",
            "“在第三块青砖后面。”",
            "顾七沉默了一会儿。",
            "“你早就知道？”",
            "“刚刚才确认。”",
        ]
    )
    records = build_chunk_records("\n".join(lines))
    dialogue = [record for record in records if record["chunk_kind"] == "dialogue"]
    assert dialogue
    target = next(record for record in dialogue if "第三块青砖后面" in record["core_text"])
    assert "第三块青砖后面" in target["text"]
    assert {"顾七", "沈青衡"}.issubset(set(target["speaker_hints"]))
    assert any("顾七问" in line for line in target["speaker_anchors"])
    assert any("沈青衡道" in line for line in target["speaker_anchors"])


def test_lexical_fallback_recalls_named_dialogue_when_dense_vectors_are_flat(monkeypatch):
    client = TestClient(app)
    _, token = register(client, "rag-lexical-fallback")
    _, body = call(client, "POST", "/api/v1/workspaces", token, json={"name": "Lexical RAG"})
    workspace = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", token, json={"name": "Lexical Project"})
    project_id = body["data"]["id"]
    prefix = f"/api/v1/projects/{project_id}"

    filler = "\n".join(
        f"第{index}段，院中风声渐紧，众人仍在讨论明日的杂役安排。" for index in range(80)
    )
    target = """
顾七问：“那枚青铜钥匙最后放到哪里了？”
“没放在柜子里。”
“那到底在哪？”
“第三块青砖后面。”
“谁知道这件事？”
“只有你和我。”
"""
    story_text = filler + "\n" + target
    code, body = call(client, "POST", f"{prefix}/stories", token, json={"title": "长篇对白", "content": story_text})
    assert code == 200, body
    story_id = body["data"]["id"]

    monkeypatch.setattr(rag_service, "selected_embedding_provider", lambda: _FlatEmbedding())
    with SessionLocal() as db:
        story = db.scalar(select(StorySource).where(StorySource.id == story_id))
        assert story
        rag_service.index_story(db, story)
        db.commit()

    results = rag_service.retrieve(
        workspace_id=workspace,
        project_id=project_id,
        query="顾七问青铜钥匙到底在哪里，对方怎么回答？",
        limit=5,
    )
    assert results
    hit = next(item for item in results if "第三块青砖后面" in item["text"])
    assert hit["lexical_score"] > 0
    assert hit["retrieval_mode"] in {"lexical", "dense+lexical"}
    assert "顾七" in hit["speaker_hints"]


def test_dialogue_retrieval_deduplicates_heavily_overlapping_chunks():
    client = TestClient(app)
    _, token = register(client, "rag-overlap")
    _, body = call(client, "POST", "/api/v1/workspaces", token, json={"name": "Overlap RAG"})
    workspace = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", token, json={"name": "Overlap Project"})
    project_id = body["data"]["id"]
    prefix = f"/api/v1/projects/{project_id}"
    story = """
顾七问：“门后是谁？”
“没人。”
“你听见脚步了吗？”
“听见了。”
“那为什么说没人？”
“因为脚步声来自楼上。”
"""
    _, body = call(client, "POST", f"{prefix}/stories", token, json={"title": "重叠对白", "content": story})
    story_id = body["data"]["id"]
    code, body = call(client, "POST", f"{prefix}/knowledge/stories/{story_id}/index", token)
    assert code == 200, body
    results = rag_service.retrieve(
        workspace_id=workspace,
        project_id=project_id,
        query="顾七问门后是谁，对方为什么说没人？",
        limit=5,
    )
    assert results
    ranges = [
        (item["source_id"], item.get("unit_start"), item.get("unit_end"))
        for item in results
    ]
    assert len(ranges) == len(set(ranges))
    assert any("脚步声来自楼上" in item["text"] for item in results)



def test_chinese_speaker_hints_are_conservative():
    assert rag_service._speaker_hints('顾七又问：“还进去吗？”') == ["顾七"]
    assert rag_service._speaker_hints('沈青衡道：“进去。”') == ["沈青衡"]
    assert rag_service._speaker_hints('顾七盯着他：“你确定？”') == []
    terms = rag_service._query_terms("顾七问青铜钥匙在哪里，对方怎么回答？")
    assert "顾七" in terms
    assert "对方怎么" not in terms
