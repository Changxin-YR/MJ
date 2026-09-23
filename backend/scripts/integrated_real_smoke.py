"""Exercise the API, persistence, worker, RAG and real media adapters once."""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.workers.tasks import process_generation


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/integrated-real")
    root.mkdir(parents=True, exist_ok=True)
    report = {"started_at": datetime.now(UTC).isoformat(), "provider": "dashscope", "steps": {}}
    client = TestClient(app)
    token = ""

    def call(method: str, path: str, **kwargs):
        response = client.request(method, "/api/v1" + path, headers={"Authorization": f"Bearer {token}"} if token else {}, **kwargs)
        body = response.json()
        if response.status_code >= 400 or not body.get("success"):
            raise RuntimeError(f"{method} {path}: {response.status_code} {body.get('error', {}).get('code')}")
        return body["data"]

    try:
        user = call("POST", "/auth/register", json={"email": f"real-{uuid4().hex[:12]}@example.com", "password": "LocalSmokePassword123", "display_name": "Real Smoke"})
        token = user["access_token"]
        workspace = call("POST", "/workspaces", json={"name": "Real Provider Smoke"})
        project = call("POST", f"/workspaces/{workspace['id']}/projects", json={"name": "Rooftop Dawn"})
        prefix = f"/projects/{project['id']}"
        story = call("POST", prefix + "/stories", json={"title": "Dawn Courier", "content": "At dawn, a courier climbs a rain-soaked rooftop. The city below is still asleep. She carries a letter that may change its fate. Looking into the rising sun, she whispers: the city is waking up. Then she takes her first step toward the light."})
        call("POST", prefix + f"/knowledge/stories/{story['id']}/index")
        sources = call("POST", prefix + "/knowledge/search", json={"query": "courier rooftop dawn"})
        report["steps"]["rag_sources"] = len(sources)
        episode = call("POST", prefix + "/episodes", json={"title": "The First Light"})
        scene = call("POST", prefix + f"/episodes/{episode['id']}/scenes", json={"heading": "EXT. CITY ROOFTOP - DAWN"})
        shot = call("POST", prefix + f"/scenes/{scene['id']}/shots", json={"description": "A lone courier on a rainy rooftop at dawn, looking at the rising sun", "dialogue": "城市醒来了。", "duration": 2, "prompt": "cinematic anime, warm rim light, clear silhouette"})
        for state in ("PLANNED", "STORYBOARD_READY"):
            shot = call("POST", prefix + f"/shots/{shot['id']}/transition", json={"expected_version": shot["version"], "target": state})
        director = call("POST", prefix + "/director/runs", json={"request": "Analyze this rooftop scene and the story context", "intent": "ANALYZE"})
        report["steps"]["director"] = {"status": director["status"], "sources": len(director["retrieved_sources"])}
        for kind in ("IMAGE", "VIDEO", "VOICE"):
            job = call("POST", prefix + f"/shots/{shot['id']}/generations", json={"kind": kind, "idempotency_key": str(uuid4())})
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                process_generation(job["id"])
                job = call("GET", prefix + f"/generation-jobs/{job['id']}")
                if job["status"] in {"SUCCEEDED", "FAILED"}:
                    break
                time.sleep(10)
            report["steps"][kind.lower()] = {"job_id": job["id"], "status": job["status"], "model": job["model"], "retry_count": job["retry_count"]}
            if job["status"] != "SUCCEEDED":
                raise RuntimeError(f"{kind} integration failed: {job['error_code']}")
            shot = call("GET", prefix + f"/shots/{shot['id']}")
            report["steps"][kind.lower()]["inspection"] = shot["inspection_json"]
        report["steps"]["assets"] = len(call("GET", prefix + "/assets"))
        report["steps"]["audit_events"] = len(call("GET", prefix + "/audit"))
        report["project_id"] = project["id"]
        report["shot_id"] = shot["id"]
        report["status"] = "PASS"
    except Exception as error:
        report["status"] = "FAIL"
        report["error_type"] = type(error).__name__
        report["error_summary"] = str(error)[:200]
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
