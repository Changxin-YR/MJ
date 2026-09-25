"""Exercise tool discovery and invocation over an actual MCP stdio session."""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import ProjectMember, User


async def verify(token: str, run_id: str) -> dict:
    env = os.environ.copy()
    env["FRAMEFORGE_MCP_ACCESS_TOKEN"] = token
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp.server"], env=env)
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = [tool.name for tool in listed.tools]
            result = await session.call_tool("frameforge_load_project_context", {"run_id": run_id})
            content = json.loads(result.content[0].text)
    return {"tool_names": names, "project_name": content["project"]["name"], "shot_count": content["shot_count"], "tool_error": result.isError}


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
    run = client.post(f"/api/v1/projects/{project_id}/director/runs", headers={"Authorization": f"Bearer {token}"}, json={"request": "Summarize project for MCP protocol check", "intent": "ANALYZE"})
    run.raise_for_status()
    run_id = run.json()["data"]["id"]
    result = asyncio.run(verify(token, run_id))
    report = {"checked_at": datetime.now(UTC).isoformat(), "run_id": run_id, **result}
    report["status"] = "PASS" if set(result["tool_names"]) == {"frameforge_load_project_context", "frameforge_retrieve_semantic_context", "frameforge_load_generation_job"} and not result["tool_error"] and result["shot_count"] >= 12 else "FAIL"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
