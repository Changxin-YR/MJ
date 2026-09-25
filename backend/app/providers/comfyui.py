"""Private ComfyUI image adapter with one fixed, allowlisted workflow."""

import re
import secrets
import time
from uuid import uuid4

import httpx

from app.config import settings
from app.providers.base import MediaResult

WORKFLOW_ID = "basic-t2i-v1"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
PRIVATE_SERVICE_URLS = {"http://comfyui:8188", "http://host.docker.internal:8189"}


def client_factory() -> httpx.Client:
    return httpx.Client(base_url=settings.comfyui_url, timeout=20, follow_redirects=False)


def controlled_workflow(prompt: str, negative_prompt: str, checkpoint: str, seed: int) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(safetensors|ckpt)", checkpoint):
        raise ValueError("ComfyUI checkpoint must be an administrator configured filename")
    if not 1 <= len(prompt) <= 3000 or len(negative_prompt) > 2000:
        raise ValueError("ComfyUI prompt exceeds allowed length")
    if not 0 <= seed < 2**63:
        raise ValueError("ComfyUI seed is out of range")
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 576, "batch_size": 1}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["1", 1]}},
        "5": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0, "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["2", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "frameforge", "images": ["6", 0]}},
    }


class ComfyUIImageProvider:
    def generate(self, prompt: str, negative_prompt: str = "") -> MediaResult:
        if settings.comfyui_url.rstrip("/") not in PRIVATE_SERVICE_URLS:
            raise ValueError("ComfyUI must use an approved private service address")
        workflow = controlled_workflow(prompt, negative_prompt, settings.comfyui_checkpoint, secrets.randbelow(2**63))
        with client_factory() as client:
            queued = client.post("/prompt", json={"prompt": workflow, "client_id": str(uuid4())})
            queued.raise_for_status()
            prompt_id = queued.json()["prompt_id"]
            if not re.fullmatch(r"[0-9a-fA-F-]{36}", prompt_id):
                raise ValueError("ComfyUI returned invalid prompt ID")
            deadline = time.monotonic() + 300
            image_info = None
            while time.monotonic() < deadline:
                response = client.get(f"/history/{prompt_id}")
                response.raise_for_status()
                history = response.json().get(prompt_id)
                if history:
                    if history.get("status", {}).get("status_str") == "error":
                        raise RuntimeError("ComfyUI workflow failed")
                    images = history.get("outputs", {}).get("7", {}).get("images", [])
                    if images:
                        image_info = images[0]
                        break
                time.sleep(1)
            if not image_info:
                raise TimeoutError("ComfyUI workflow timed out")
            filename = image_info.get("filename", "")
            subfolder = image_info.get("subfolder", "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.png", filename) or subfolder not in {"", None} or image_info.get("type") != "output":
                raise ValueError("ComfyUI output path is not allowed")
            with client.stream("GET", "/view", params={"filename": filename, "subfolder": "", "type": "output"}) as response:
                response.raise_for_status()
                parts = []
                size = 0
                for part in response.iter_bytes():
                    size += len(part)
                    if size > MAX_IMAGE_BYTES:
                        raise ValueError("ComfyUI output exceeds size limit")
                    parts.append(part)
        content = b"".join(parts)
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("ComfyUI output is not PNG")
        return MediaResult(content=content, mime="image/png", model=f"comfyui:{WORKFLOW_ID}", width=1024, height=576)
