"""Index and query the final demo story with the configured embedding provider."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import StorySource
from app.providers.embeddings import selected_embedding_provider
from app.rag.service import build_chunk_records, collection_name, index_story, retrieve


def cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    return numerator / max(left_norm * right_norm, 1e-12)


def main() -> None:
    project_id = sys.argv[1]
    output = Path(sys.argv[2])
    with SessionLocal() as db:
        story = db.scalar(select(StorySource).where(StorySource.project_id == project_id).order_by(StorySource.created_at))
        if story is None:
            raise ValueError("Project has no story")
        document = index_story(db, story)
        db.commit()
        workspace_id = story.workspace_id
        version_id = document.current_version_id
    query = "米拉在暴雨中的城市寻找钟楼与黎明"
    results = retrieve(workspace_id=workspace_id, project_id=project_id, query=query)
    provider = selected_embedding_provider()
    dialogue_story = """沈青衡停在石门前。
顾七问：“还进去吗？”
“进去。”
“为什么？”
“因为钥匙在里面。”
“确定？”
“确定。”"""
    dialogue_records = build_chunk_records(dialogue_story, known_speakers=["顾七", "沈青衡"])
    probe_texts = [
        "顾七问为什么还要进石门，对方怎么回答？",
        "顾七问：“还进去吗？”“为什么？”“因为钥匙在里面。”",
        "许棠问：“药田明天浇水吗？”“卯时前用山泉浇。”",
    ]
    if hasattr(provider, "embed_many"):
        probe_vectors = provider.embed_many(probe_texts)
    else:
        probe_vectors = [provider.embed(text) for text in probe_texts]
    relevant_similarity = cosine(probe_vectors[0], probe_vectors[1])
    irrelevant_similarity = cosine(probe_vectors[0], probe_vectors[2])
    dialogue_probe = {
        "dialogue_chunk_present": any(record["chunk_kind"] == "dialogue" for record in dialogue_records),
        "subjectless_context_preserved": any(
            record["chunk_kind"] == "dialogue"
            and "顾七问" in record["text"]
            and "钥匙在里面" in record["text"]
            for record in dialogue_records
        ),
        "relevant_similarity": relevant_similarity,
        "irrelevant_similarity": irrelevant_similarity,
        "semantic_margin_positive": relevant_similarity > irrelevant_similarity,
    }
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "provider": type(provider).__name__,
        "model": provider.model,
        "dimensions": provider.dimensions,
        "collection": collection_name(provider),
        "document_id": document.id,
        "version_id": version_id,
        "query": query,
        "result_count": len(results),
        "source_ids": [result["source_id"] for result in results],
        "all_results_scoped": all(result["document_id"] == document.id for result in results),
        "dialogue_probe": dialogue_probe,
        "status": "PASS"
        if (
            results
            and all(result["document_id"] == document.id for result in results)
            and all(
                (
                    dialogue_probe["dialogue_chunk_present"],
                    dialogue_probe["subjectless_context_preserved"],
                    dialogue_probe["semantic_margin_positive"],
                )
            )
        )
        else "FAIL",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
