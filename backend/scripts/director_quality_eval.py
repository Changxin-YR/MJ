"""Evaluate real read-only Director answers against grounded story facts."""

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

CASES = (
    {
        "id": "signal_key",
        "question": "只根据故事资料回答：米拉在隧道中找到什么，后来在哪里使用？",
        "required": (("钥匙",), ("钟楼", "发射器")),
    },
    {
        "id": "deadline",
        "question": "只根据故事资料回答：信里要求米拉在什么时间之前完成任务？",
        "required": (("太阳升起", "黎明", "日出"),),
    },
    {
        "id": "sender",
        "question": "只根据故事资料回答：信上是否写了寄件人的姓名？若没有，请明确说明。",
        "required": (("没有", "未写", "未提", "匿名", "无寄件人"),),
    },
)


def main() -> None:
    project_id = sys.argv[1]
    output = Path(sys.argv[2])
    with SessionLocal() as db:
        email = db.scalar(select(User.email).join(ProjectMember, ProjectMember.user_id == User.id).where(ProjectMember.project_id == project_id, ProjectMember.role == "OWNER"))
    if not email:
        raise ValueError("Demo owner missing")
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "DemoPassword12345!"})
    response.raise_for_status()
    token = response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    results = []
    for case in CASES:
        response = client.post(f"/api/v1/projects/{project_id}/director/runs", headers=headers, json={"intent": "ANALYZE", "request": case["question"]})
        response.raise_for_status()
        run = response.json()["data"]
        summary = run["decision_summary"]
        checks = {
            "completed": run["status"] == "COMPLETED",
            "grounded_answer": all(any(term in summary for term in group) for group in case["required"]),
            "sources_present": bool(run["retrieved_sources"]),
            "read_only": not run["generation_jobs"] and not run["pending_actions"] and all(call["name"] in {"load_project_context", "retrieve_semantic_context"} for call in run["tool_calls"]),
        }
        results.append({"id": case["id"], "question": case["question"], "run_id": run["id"], "summary": summary, "source_ids": [source["source_id"] for source in run["retrieved_sources"]], "trace_id": run["trace_id"], "checks": checks, "status": "PASS" if all(checks.values()) else "FAIL"})
        print(f"{case['id']}: {results[-1]['status']}", flush=True)
    report = {"checked_at": datetime.now(UTC).isoformat(), "project_id": project_id, "llm_model": settings.dashscope_llm_model, "embedding_model": settings.dashscope_embedding_model, "results": results, "status": "PASS" if all(result["status"] == "PASS" for result in results) else "FAIL"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
