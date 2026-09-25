"""Index and query the final demo story with the configured embedding provider."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import StorySource
from app.providers.embeddings import selected_embedding_provider
from app.rag.service import collection_name, index_story, retrieve


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
        "status": "PASS" if results and all(result["document_id"] == document.id for result in results) else "FAIL",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
