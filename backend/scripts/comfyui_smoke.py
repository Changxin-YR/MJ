"""Run the configured private ComfyUI adapter and save one real output."""

import hashlib
import io
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from app.config import settings
from app.providers.comfyui import WORKFLOW_ID, ComfyUIImageProvider


def main() -> None:
    target = Path(os.environ.get("EVIDENCE_DIR", "/evidence"))
    target.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = ComfyUIImageProvider().generate(
        "cinematic still, a lone courier in a red scarf on a city rooftop at sunrise, natural light, detailed",
        "extra fingers, duplicate person, text, watermark",
    )
    image = Image.open(io.BytesIO(result.content))
    image.verify()
    image = Image.open(io.BytesIO(result.content))
    if image.size != (result.width, result.height):
        raise RuntimeError("ComfyUI output dimensions do not match the workflow")
    (target / "sample.png").write_bytes(result.content)
    report = {
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "endpoint": settings.comfyui_url,
        "checkpoint": settings.comfyui_checkpoint,
        "workflow": WORKFLOW_ID,
        "image_width": image.width,
        "image_height": image.height,
        "image_bytes": len(result.content),
        "image_sha256": hashlib.sha256(result.content).hexdigest(),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "result": "PASS",
    }
    (target / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
