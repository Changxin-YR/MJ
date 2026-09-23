"""Re-render an existing approved episode without another model call."""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Episode, ProjectMember, User


def main() -> None:
    project_id = sys.argv[1]
    root = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/rerender")
    root.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        email = db.scalar(select(User.email).join(ProjectMember, ProjectMember.user_id == User.id).where(ProjectMember.project_id == project_id, ProjectMember.role == "OWNER"))
        episode_id = db.scalar(select(Episode.id).where(Episode.project_id == project_id).order_by(Episode.episode_no))
    if not email or not episode_id:
        raise ValueError("Demo project or owner missing")
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "DemoPassword12345!"})
    login.raise_for_status()
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    prefix = f"/api/v1/projects/{project_id}"

    def call(method: str, path: str, **kwargs):
        response = client.request(method, prefix + path, headers=headers, **kwargs)
        response.raise_for_status()
        body = response.json()
        if not body["success"]:
            raise RuntimeError(f"{method} {path}: {body['error']['code']}")
        return body["data"]

    timeline = call("GET", f"/episodes/{episode_id}/timeline")
    timeline = call("POST", f"/timelines/{timeline['id']}/sync", json={"expected_version": timeline["version"]})
    timeline = call("POST", f"/timelines/{timeline['id']}/render", json={"expected_version": timeline["version"]})
    response = client.get(prefix + f"/assets/{timeline['final_asset_id']}/content", headers=headers)
    response.raise_for_status()
    path = root / "final.mp4"
    path.write_bytes(response.content)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,width,height", "-of", "json", str(path)], capture_output=True, text=True, check=True)
    report = {"rendered_at": datetime.now(UTC).isoformat(), "project_id": project_id, "episode_id": episode_id, "final_asset_id": timeline["final_asset_id"], "bytes": len(response.content), "ffprobe": json.loads(probe.stdout), "status": "PASS"}
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lineage = call("GET", f"/assets/{timeline['final_asset_id']}/lineage")
    (root / "lineage.json").write_text(json.dumps(lineage, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
