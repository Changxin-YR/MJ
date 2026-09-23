"""Repair the four rejected demo shots through the normal API and real workers."""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Episode, ProjectMember, Scene, Shot, User

REPAIRS = {
    1: {
        "description": "Before dawn, a patchy citywide power outage has darkened many districts. Pockets of buildings still glow from backup generators and scattered emergency streetlights beneath a full moon. Mira, one courier in a blue raincoat with an orange messenger bag, surveys the city from a wet rooftop.",
        "prompt": "Wide establishing shot of a rain-soaked city during a patchy power outage. Many districts are dark, while scattered residential backup generators and a few emergency streetlights remain lit. A bright full moon is visible before dawn. Exactly one young woman with short dark hair, blue raincoat and one orange messenger bag stands on a roof. Hand-painted cinematic graphic novel.",
        "negative_prompt": "fully illuminated skyline, crowded city, bright daylight, extra people, duplicate person, text, watermark",
    },
    2: {
        "description": "On a wet rooftop, Mira opens the flap of the single orange messenger bag she is wearing across her shoulder and takes out one sealed letter. No other bag or parcel is present.",
        "prompt": "Exactly one young woman with short dark hair and a blue raincoat. One orange canvas messenger bag only, worn across her body, its flap open while she retrieves one sealed letter. Her hands interact with the orange bag she wears. Close cinematic graphic novel shot, rainy roof, consistent character.",
        "negative_prompt": "second bag, brown bag, leather satchel, backpack, suitcase, duplicate purse, bag on ground, extra people, duplicate woman, text, watermark",
    },
    3: {
        "description": "Mira alone reads the hand-drawn map from the letter on a dark rooftop. Only one woman is visible; the unlit city skyline is behind her.",
        "prompt": "Single-person close shot: exactly one Mira, a young woman with short dark hair in a blue raincoat with one orange messenger bag. She holds a small unfolded paper map in both hands and studies it. Empty rooftop around her, dark powerless city behind. No reflection or silhouette resembling a second person. Hand-painted cinematic graphic novel.",
        "negative_prompt": "second person, duplicate woman, twin, clone, reflection, mirror, shadow person, extra head, additional figure, lit windows, text, watermark",
    },
    5: {
        "description": "Mira arrives at the city's ornate neo-Gothic clock tower as storm clouds part around the first orange light at the horizon. The tall square stone tower has large round clock faces and carved trim, matching the tower seen again at sunrise. A patchy outage leaves some districts dark while backup lights remain on.",
        "prompt": "A tall ornate neo-Gothic square stone clock tower with large round clock faces and carved trim, the same landmark later seen at sunrise. Storm clouds part around the first orange light at the horizon. Patchy power outage leaves some districts dark while backup generators and emergency streetlights remain lit. Exactly one woman with short dark hair, blue raincoat and one orange messenger bag stands before the tower. Cinematic hand-painted graphic novel.",
        "negative_prompt": "plain modern tower, cylindrical tower, small village clock, fully illuminated skyline, neon city, multiple people, text, watermark",
    },
}

PROJECT_ID = "9f63776f-b512-4e68-b21b-5e20266a159b"


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/final-repair")
    root.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        email = db.scalar(select(User.email).join(ProjectMember, ProjectMember.user_id == User.id).where(ProjectMember.project_id == PROJECT_ID, ProjectMember.role == "OWNER"))
        episode_id = db.scalar(select(Episode.id).where(Episode.project_id == PROJECT_ID).order_by(Episode.episode_no))
        ordered = db.execute(select(Shot.id).join(Scene, Scene.id == Shot.scene_id).where(Shot.project_id == PROJECT_ID).order_by(Scene.scene_no, Shot.shot_no)).scalars().all()
    if not email or not episode_id or len(ordered) != 12:
        raise RuntimeError("Demo project is missing or has unexpected shot count")
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "DemoPassword12345!"})
    login.raise_for_status()
    token = login.json()["data"]["access_token"]
    prefix = f"/api/v1/projects/{PROJECT_ID}"
    headers = {"Authorization": f"Bearer {token}"}
    only_argument = next((arg for arg in sys.argv[2:] if arg.startswith("--only=")), None)
    selected = {number: changes for number, changes in REPAIRS.items() if only_argument is None or number in {int(value) for value in only_argument.split("=", 1)[1].split(",")}}
    if not selected:
        raise ValueError("No matching demo shots selected")
    report = {"project_id": PROJECT_ID, "started_at": datetime.now(UTC).isoformat(), "repairs": {}, "status": "RUNNING"}

    def save() -> None:
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def call(method: str, path: str, **kwargs):
        response = client.request(method, prefix + path, headers=headers, **kwargs)
        body = response.json()
        if response.status_code >= 400 or not body.get("success"):
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code} {body.get('error', {}).get('code')}")
        return body["data"]

    def shot(number: int) -> dict:
        return call("GET", f"/shots/{ordered[number - 1]}")

    def await_jobs(job_ids: dict[int, str], label: str) -> None:
        deadline = time.monotonic() + 1200
        last = None
        while time.monotonic() < deadline:
            jobs = {number: call("GET", f"/generation-jobs/{job_id}") for number, job_id in job_ids.items()}
            states = {number: row["status"] for number, row in jobs.items()}
            if states != last:
                print(f"{label}: {states}", flush=True)
                last = states
            if any(row["status"] == "FAILED" for row in jobs.values()):
                raise RuntimeError(f"{label} failed: {[(n, r['error_code']) for n, r in jobs.items() if r['status'] == 'FAILED']}")
            if all(row["status"] == "SUCCEEDED" for row in jobs.values()):
                return
            time.sleep(8)
        raise TimeoutError(f"{label} generation timed out")

    if len(sys.argv) > 2 and sys.argv[2] == "--context-only":
        for number, changes in selected.items():
            current = shot(number)
            if any(current[key] != value for key, value in changes.items()):
                call("PATCH", f"/shots/{current['id']}", json={"expected_version": current["version"], **changes})
        report["status"] = "PASS"
        report["finished_at"] = datetime.now(UTC).isoformat()
        save()
        return

    if "--approve-only" in sys.argv[2:]:
        for number in selected:
            current = shot(number)
            if current["status"] != "REVIEW_REQUIRED" or (current["inspection_json"] or {}).get("status") != "PASS":
                raise RuntimeError(f"Shot {number} is not ready for approval")
            call("POST", f"/shots/{current['id']}/transition", json={"expected_version": current["version"], "target": "APPROVED"})
        report["status"] = "PASS"
        report["finished_at"] = datetime.now(UTC).isoformat()
        save()
        return

    try:
        for number, changes in selected.items():
            current = shot(number)
            if current["status"] == "APPROVED":
                current = call("POST", f"/shots/{current['id']}/transition", json={"expected_version": current["version"], "target": "REJECTED"})
            if current["status"] == "REJECTED":
                current = call("POST", f"/shots/{current['id']}/transition", json={"expected_version": current["version"], "target": "PLANNED"})
            if current["status"] in {"PLANNED", "REVIEW_REQUIRED"}:
                current = call("PATCH", f"/shots/{current['id']}", json={"expected_version": current["version"], **changes})
                if current["status"] == "PLANNED":
                    call("POST", f"/shots/{current['id']}/transition", json={"expected_version": current["version"], "target": "STORYBOARD_READY"})
            report["repairs"][number] = {"shot_id": current["id"], "attempts": []}
        save()

        pending = set(selected) if only_argument else {number for number in selected if shot(number)["inspection_json"]["status"] != "PASS"}
        for attempt in range(1, 4):
            if not pending:
                break
            jobs = {number: call("POST", f"/shots/{ordered[number - 1]}/generations", json={"kind": "IMAGE", "idempotency_key": str(uuid4())})["id"] for number in sorted(pending)}
            await_jobs(jobs, f"IMAGE attempt {attempt}")
            for number in sorted(pending):
                current = shot(number)
                inspection = current["inspection_json"]
                report["repairs"][number]["attempts"].append({"kind": "IMAGE", "job_id": jobs[number], "asset_id": current["current_image_asset_id"], "inspection": inspection})
            pending = {number for number in pending if shot(number)["inspection_json"]["status"] != "PASS"}
            save()
        if pending:
            raise RuntimeError(f"Image inspection failed after three attempts: {sorted(pending)}")

        pending = set(selected)
        for attempt in range(1, 4):
            if not pending:
                break
            jobs = {number: call("POST", f"/shots/{ordered[number - 1]}/generations", json={"kind": "VIDEO", "idempotency_key": str(uuid4())})["id"] for number in sorted(pending)}
            await_jobs(jobs, f"VIDEO attempt {attempt}")
            for number in sorted(pending):
                current = shot(number)
                inspection = current["inspection_json"]
                report["repairs"][number]["attempts"].append({"kind": "VIDEO", "job_id": jobs[number], "asset_id": current["current_video_asset_id"], "inspection": inspection})
            pending = {number for number in pending if shot(number)["inspection_json"]["status"] != "PASS"}
            save()
        if pending:
            raise RuntimeError(f"Video inspection failed after three attempts: {sorted(pending)}")

        for number in selected:
            current = shot(number)
            if current["inspection_json"]["status"] != "PASS":
                raise RuntimeError(f"Shot {number} is not visually approved")
            call("POST", f"/shots/{current['id']}/transition", json={"expected_version": current["version"], "target": "APPROVED"})
        report["status"] = "PASS"
    except Exception as error:
        report["status"] = "FAIL"
        report["error"] = f"{type(error).__name__}: {error}"[:500]
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        save()


if __name__ == "__main__":
    main()
