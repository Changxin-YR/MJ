"""Create one 60-second comic-drama demo through real FrameForge APIs and workers."""

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

STORY = """断电后的城市，在黎明前像一张没有写完的画。信使米拉穿着蓝色雨衣，背着橙色邮差包，接到一封没有寄件人的信。信中只有一张旧车站的坐标和一句话：在太阳升起之前，把光送回钟楼。她越过湿滑的屋顶，穿过停摆的车站，在隧道深处找到一枚信号钥匙。钟楼上的倒计时只剩五秒。米拉把钥匙放进发射器，灯光沿着街道一盏盏亮起。她望着重新苏醒的城市，终于明白信的寄件人，是那个从未放弃这座城的自己。"""

BEATS = [
    ("A blacked-out city before dawn, wet rooftops and dark windows, Mira the courier on a roof in silhouette", "天亮前，城市断电了。"),
    ("Mira discovers an old sealed letter beneath her orange messenger bag on a rainy rooftop", "一封信，指向钟楼。"),
    ("Close view of Mira reading a hand-drawn map in the letter, city skyline behind her", "太阳升起前，送回光。"),
    ("Mira runs across a rain-soaked rooftop, orange messenger bag swinging, city below", "我得赶在黎明之前。"),
    ("Mira reaches a silent clock tower under storm clouds, no lights in the city", "钟楼就在前面。"),
    ("Mira descends into an abandoned train station, blue raincoat and orange bag distinct", "旧车站，藏着答案。"),
    ("Mira follows a faint blue signal through an empty tunnel, atmospheric graphic novel art", "信号还没有消失。"),
    ("Mira finds a small brass signal key beside a dormant machine in the tunnel", "找到了，就是它。"),
    ("Mira climbs the narrow stairs inside the clock tower carrying the brass key", "只剩最后几秒。"),
    ("Mira inserts the key into the rooftop signal beacon as clouds begin to part", "三、二、一，启动。"),
    ("A wave of warm light spreads from the clock tower across the dark city, Mira watches", "光，终于回来了。"),
    ("At sunrise Mira stands on the clock tower rooftop, city lights glowing below, hopeful finale", "这座城，醒来了。"),
]


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/final-demo")
    root.mkdir(parents=True, exist_ok=True)
    (root / "story.txt").write_text(STORY, encoding="utf-8")
    report = {"started_at": datetime.now(UTC).isoformat(), "provider": "dashscope", "shot_count": len(BEATS), "target_seconds": len(BEATS) * 5, "stages": {}}
    client = TestClient(app)
    token = ""

    def call(method: str, path: str, **kwargs):
        response = client.request(method, "/api/v1" + path, headers={"Authorization": f"Bearer {token}"} if token else {}, **kwargs)
        body = response.json()
        if response.status_code >= 400 or not body.get("success"):
            raise RuntimeError(f"{method} {path}: {response.status_code} {body.get('error', {}).get('code')}")
        return body["data"]

    try:
        identity = call("POST", "/auth/register", json={"email": f"demo-{uuid4().hex[:12]}@example.com", "password": "DemoPassword12345!", "display_name": "Demo Producer"})
        token = identity["access_token"]
        workspace = call("POST", "/workspaces", json={"name": "FrameForge Showcase"})
        project = call("POST", f"/workspaces/{workspace['id']}/projects", json={"name": "黎明信使 · Dawn Courier"})
        prefix = f"/projects/{project['id']}"
        story = call("POST", prefix + "/stories", json={"title": "黎明信使", "content": STORY})
        call("POST", prefix + f"/knowledge/stories/{story['id']}/index")
        character = call("POST", prefix + "/characters", json={"name": "米拉 Mira", "background": "A courier who refuses to give up on her city", "dna": {"face": "young woman, determined eyes", "hair": "short dark hair", "costume": "blue raincoat, orange messenger bag", "style": "cinematic hand-painted graphic novel, moody rain and warm sunrise", "voice": "calm, determined young woman", "prompt_anchor": "Mira is a young woman with short dark hair, blue raincoat and orange messenger bag; preserve these features in every frame", "negative_prompt": "extra people, distorted face, text, watermark"}})
        version = character["versions"][0]
        call("POST", prefix + f"/characters/{character['id']}/versions/{version['id']}/activate", json={"expected_version": character["version"]})
        episode = call("POST", prefix + "/episodes", json={"title": "第一集 · 把光送回城市", "synopsis": STORY})
        shots = []
        for scene_index, heading in enumerate(("EXT. ROOFTOPS - PRE-DAWN", "INT. ABANDONED STATION - DAWN", "INT/EXT. CLOCK TOWER - SUNRISE")):
            scene = call("POST", prefix + f"/episodes/{episode['id']}/scenes", json={"heading": heading, "description": "Mira follows the letter and restores the city's light."})
            for description, dialogue in BEATS[scene_index * 4:(scene_index + 1) * 4]:
                shot = call("POST", prefix + f"/scenes/{scene['id']}/shots", json={"description": description, "dialogue": dialogue, "duration": 5, "character_ids": [character["id"]], "prompt": "Cinematic hand-painted graphic novel, consistent Mira: young woman, short dark hair, blue raincoat, orange messenger bag, dramatic lighting, no text", "negative_prompt": "extra people, malformed hands, text, watermark"})
                for state in ("PLANNED", "STORYBOARD_READY"):
                    shot = call("POST", prefix + f"/shots/{shot['id']}/transition", json={"expected_version": shot["version"], "target": state})
                shots.append(shot)
        analysis = call("POST", prefix + "/director/runs", json={"request": "分析米拉如何在黎明前把光送回城市，并引用故事来源。", "intent": "ANALYZE"})
        report["stages"]["director_analysis"] = {"status": analysis["status"], "retrieved_sources": len(analysis["retrieved_sources"])}
        run = call("POST", prefix + "/director/runs", json={"request": "为第一个镜头生成画面。", "intent": "GENERATE_IMAGE", "shot_id": shots[0]["id"]})
        pending = run["pending_actions"][0]
        call("POST", prefix + f"/pending-actions/{pending['id']}/approve")
        run = call("POST", f"/director/runs/{run['id']}/resume")
        image_jobs = [run["generation_jobs"][0]["id"]]
        for shot in shots[1:]:
            image_jobs.append(call("POST", prefix + f"/shots/{shot['id']}/generations", json={"kind": "IMAGE", "idempotency_key": str(uuid4())})["id"])

        def await_jobs(kind: str, ids: list[str], timeout: int = 1200) -> None:
            deadline = time.monotonic() + timeout
            last = None
            while time.monotonic() < deadline:
                jobs = [call("GET", prefix + f"/generation-jobs/{job_id}") for job_id in ids]
                states = {status: sum(job["status"] == status for job in jobs) for status in {job["status"] for job in jobs}}
                if states != last:
                    print(f"{kind}: {states}", flush=True)
                    last = states
                if any(job["status"] == "FAILED" for job in jobs):
                    failures = [(job["id"], job["error_code"]) for job in jobs if job["status"] == "FAILED"]
                    report["stages"][kind.lower()] = {"status": "FAIL", "failures": failures, "states": states}
                    raise RuntimeError(f"{kind} jobs failed: {failures}")
                if all(job["status"] == "SUCCEEDED" for job in jobs):
                    report["stages"][kind.lower()] = {"status": "PASS", "count": len(jobs), "providers": sorted({job["provider"] for job in jobs}), "models": sorted({job["model"] for job in jobs})}
                    return
                time.sleep(8)
            raise TimeoutError(f"{kind} jobs did not finish in {timeout}s")

        await_jobs("IMAGE", image_jobs)
        video_jobs = [call("POST", prefix + f"/shots/{shot['id']}/generations", json={"kind": "VIDEO", "idempotency_key": str(uuid4())})["id"] for shot in shots]
        await_jobs("VIDEO", video_jobs)
        voice_jobs = [call("POST", prefix + f"/shots/{shot['id']}/generations", json={"kind": "VOICE", "idempotency_key": str(uuid4())})["id"] for shot in shots]
        await_jobs("VOICE", voice_jobs)
        inspection = []
        for shot in shots:
            current = call("GET", prefix + f"/shots/{shot['id']}")
            inspection.append({"shot_id": shot["id"], "status": current["inspection_json"]["status"], "method": current["inspection_json"]["method"]})
            call("POST", prefix + f"/shots/{shot['id']}/transition", json={"expected_version": current["version"], "target": "APPROVED"})
        report["stages"]["inspection"] = inspection
        timeline = call("POST", prefix + f"/episodes/{episode['id']}/timeline")
        timeline = call("POST", prefix + f"/timelines/{timeline['id']}/sync", json={"expected_version": timeline["version"]})
        timeline = call("POST", prefix + f"/timelines/{timeline['id']}/render", json={"expected_version": timeline["version"]})
        asset_id = timeline["final_asset_id"]
        video = client.get("/api/v1" + prefix + f"/assets/{asset_id}/content", headers={"Authorization": f"Bearer {token}"})
        if video.status_code != 200:
            raise RuntimeError(f"Final asset download: HTTP {video.status_code}")
        final_path = root / "final.mp4"
        final_path.write_bytes(video.content)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height", "-of", "json", str(final_path)], capture_output=True, text=True, check=True)
        report["stages"]["final_video"] = {"status": "PASS", "asset_id": asset_id, "bytes": len(video.content), "ffprobe": json.loads(probe.stdout)}
        lineage = call("GET", prefix + f"/assets/{asset_id}/lineage")
        (root / "lineage.json").write_text(json.dumps(lineage, ensure_ascii=False, indent=2), encoding="utf-8")
        report["stages"]["audit_count"] = len(call("GET", prefix + "/audit"))
        report["project_id"] = project["id"]
        report["episode_id"] = episode["id"]
        report["status"] = "PASS"
    except Exception as error:
        report["status"] = "FAIL"
        report["error_type"] = type(error).__name__
        report["error_summary"] = str(error)[:300]
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
