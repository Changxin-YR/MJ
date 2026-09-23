"""Exercise real ComfyUI generation through the HTTP API, outbox, worker, and asset store."""

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx


def main() -> None:
    with httpx.Client(base_url=os.environ.get("FRAMEFORGE_API_URL", "http://api:8000"), timeout=20) as client:
        token = ""

        def call(method: str, path: str, **kwargs):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = client.request(method, path, headers=headers, **kwargs)
            response.raise_for_status()
            body = response.json()
            if not body["success"]:
                raise RuntimeError(body)
            return body["data"]

        suffix = uuid4().hex[:12]
        auth = call("POST", "/api/v1/auth/register", json={"email": f"comfy-smoke-{suffix}@example.com", "password": "StrongPassword123!", "display_name": "Comfy Smoke"})
        token = auth["access_token"]
        workspace = call("POST", "/api/v1/workspaces", json={"name": "ComfyUI Live Verification"})["id"]
        project = call("POST", f"/api/v1/workspaces/{workspace}/projects", json={"name": "ComfyUI Image"})["id"]
        prefix = f"/api/v1/projects/{project}"
        episode = call("POST", f"{prefix}/episodes", json={"title": "Live Image"})["id"]
        scene = call("POST", f"{prefix}/episodes/{episode}/scenes", json={"heading": "EXT. ROOFTOP - DAWN"})["id"]
        shot = call("POST", f"{prefix}/scenes/{scene}/shots", json={"description": "A courier wearing a red scarf looks across the city at dawn", "duration": 2})
        for state in ("PLANNED", "STORYBOARD_READY"):
            shot = call("POST", f"{prefix}/shots/{shot['id']}/transition", json={"expected_version": shot["version"], "target": state})
        job = call("POST", f"{prefix}/shots/{shot['id']}/generations", json={"kind": "IMAGE", "idempotency_key": str(uuid4())})
        if job["provider"] != "comfyui":
            raise RuntimeError(f"Expected comfyui provider, got {job['provider']}")

        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            current = call("GET", f"{prefix}/generation-jobs/{job['id']}")
            if current["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            time.sleep(2)
        else:
            raise TimeoutError("ComfyUI job did not finish")
        if current["status"] != "SUCCEEDED":
            raise RuntimeError(f"ComfyUI job ended in {current['status']}: {current.get('error_code')}")
        shot = call("GET", f"{prefix}/shots/{shot['id']}")
        asset = client.get(f"{prefix}/assets/{shot['current_image_asset_id']}/content", headers={"Authorization": f"Bearer {token}"})
        asset.raise_for_status()
        if not asset.content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Generated asset is not PNG")

    report = {
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "project_id": project,
        "shot_id": shot["id"],
        "job_id": job["id"],
        "provider": job["provider"],
        "status": current["status"],
        "asset_id": shot["current_image_asset_id"],
        "asset_bytes": len(asset.content),
        "asset_sha256": hashlib.sha256(asset.content).hexdigest(),
        "result": "PASS",
    }
    target = Path(os.environ.get("EVIDENCE_DIR", "/evidence"))
    target.mkdir(parents=True, exist_ok=True)
    (target / "job-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
