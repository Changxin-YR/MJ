"""Run the Director's read-only analysis with real LLM and real RAG."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import ProjectMember, User


def main() -> None:
    project_id = sys.argv[1]
    output = Path(sys.argv[2])
    with SessionLocal() as db:
        email = db.scalar(select(User.email).join(ProjectMember, ProjectMember.user_id == User.id).where(ProjectMember.project_id == project_id, ProjectMember.role == "OWNER"))
    if not email:
        raise ValueError("Demo owner missing")
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "DemoPassword12345!"})
    login.raise_for_status()
    token = login.json()["data"]["access_token"]
    response = client.post(
        f"/api/v1/projects/{project_id}/director/runs",
        headers={"Authorization": f"Bearer {token}"},
        json={"request": "根据故事资料，简述米拉、钟楼和停电之间的关系，并给出本集制作关注点。", "intent": "ANALYZE"},
    )
    response.raise_for_status()
    run = response.json()["data"]
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "run_id": run["id"],
        "status": run["status"],
        "llm_model": settings.dashscope_llm_model,
        "embedding_model": settings.dashscope_embedding_model,
        "summary": run["decision_summary"],
        "retrieved_sources": run["retrieved_sources"],
        "tool_calls": run["tool_calls"],
        "audit_trace_id": run["trace_id"],
        "result": "PASS" if run["status"] == "COMPLETED" and run["decision_summary"] and run["retrieved_sources"] and all(call["name"] in {"load_project_context", "retrieve_semantic_context"} for call in run["tool_calls"]) and not run["generation_jobs"] and not run["pending_actions"] else "FAIL",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if report["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
