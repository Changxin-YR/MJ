"""One bounded real-provider smoke run; credentials are never written to evidence."""

import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.asset.storage import validate
from app.providers.dashscope import (
    DashScopeImageProvider,
    DashScopeTTSProvider,
    DashScopeVideoProvider,
)


def save(root: Path, name: str, result) -> dict:
    validate(result)
    path = root / name
    path.write_bytes(result.content)
    return {"file": name, "mime": result.mime, "model": result.model, "bytes": len(result.content), "sha256": hashlib.sha256(result.content).hexdigest(), "width": result.width, "height": result.height, "duration": result.duration}


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/provider-smoke")
    root.mkdir(parents=True, exist_ok=True)
    report = {"started_at": datetime.now(UTC).isoformat(), "provider": "dashscope", "results": {}}
    try:
        image = DashScopeImageProvider().generate("Cinematic anime storyboard, a lone courier on a city rooftop at dawn, warm rim light, clear silhouette, no text")
        report["results"]["image"] = save(root, "image.png", image)
        print("image: validated", flush=True)
        voice = DashScopeTTSProvider().synthesize("城市醒来了。", 2)
        report["results"]["voice"] = save(root, "voice.wav", voice)
        print("voice: validated", flush=True)
        provider = DashScopeVideoProvider()
        remote_id = provider.submit(image.content, 2, "The courier looks toward the rising sun. Gentle camera movement, cinematic anime.")
        report["results"]["video_task_id"] = remote_id
        print("video: submitted", flush=True)
        deadline = time.monotonic() + 540
        while time.monotonic() < deadline:
            status = provider.get_status(remote_id)
            print(f"video: {status}", flush=True)
            if status == "SUCCEEDED":
                report["results"]["video"] = save(root, "video.mp4", provider.fetch_result(remote_id))
                break
            if status in {"FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}:
                raise RuntimeError(f"Video task status: {status}")
            time.sleep(10)
        else:
            raise TimeoutError("Video smoke poll exceeded 9 minutes")
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
