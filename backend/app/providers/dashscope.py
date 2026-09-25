"""DashScope media adapters. All remote bytes pass through asset validation later."""

import base64
import io
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from app.config import settings
from app.providers.base import MediaResult

MAX_REMOTE_BYTES = 50 * 1024 * 1024


def _api(method: str, path: str, *, payload: dict | None = None, asynchronous: bool = False) -> dict:
    if not settings.dashscope_api_key:
        raise ValueError("DashScope API key is not configured")
    headers = {"Authorization": f"Bearer {settings.dashscope_api_key}"}
    if asynchronous:
        headers["X-DashScope-Async"] = "enable"
    try:
        with httpx.Client(timeout=httpx.Timeout(120, connect=15), follow_redirects=False) as client:
            response = client.request(method, f"{settings.dashscope_base_url.rstrip('/')}/{path.lstrip('/')}", headers=headers, json=payload)
        if response.status_code in {408, 429} or response.status_code >= 500:
            raise TimeoutError(f"DashScope temporary HTTP {response.status_code}")
        if response.is_error:
            code = response.json().get("code", "HTTP_ERROR") if response.headers.get("content-type", "").startswith("application/json") else "HTTP_ERROR"
            raise ValueError(f"DashScope {code} (HTTP {response.status_code})")
        body = response.json()
    except httpx.TransportError as error:
        raise TimeoutError("DashScope connection failed") from error
    if isinstance(body, dict) and body.get("code"):
        raise ValueError(f"DashScope {body['code']}")
    return body


def _download(url: str) -> bytes:
    host = (urlparse(url).hostname or "").lower()
    if urlparse(url).scheme not in {"https", "http"} or not host.endswith(".aliyuncs.com"):
        raise ValueError("Provider returned an untrusted asset URL")
    try:
        with httpx.Client(timeout=httpx.Timeout(120, connect=15), follow_redirects=False) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_REMOTE_BYTES:
                        raise ValueError("Provider asset exceeds size limit")
                    chunks.append(chunk)
        return b"".join(chunks)
    except httpx.HTTPStatusError as error:
        if error.response.status_code in {408, 429} or error.response.status_code >= 500:
            raise TimeoutError(f"Provider asset temporary HTTP {error.response.status_code}") from None
        raise ValueError(f"Provider asset HTTP {error.response.status_code}") from None
    except httpx.TransportError as error:
        raise TimeoutError("Provider asset download failed") from error


class DashScopeImageProvider:
    def generate(self, prompt: str, negative_prompt: str = "") -> MediaResult:
        body = _api("POST", "services/aigc/multimodal-generation/generation", payload={
            "model": settings.dashscope_image_model,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": {"prompt_extend": False, "watermark": False, "n": 1, "negative_prompt": negative_prompt, "size": settings.dashscope_image_size},
        })
        content = body["output"]["choices"][0]["message"]["content"]
        image_url = next(item["image"] for item in content if "image" in item)
        with Image.open(io.BytesIO(_download(image_url))) as image:
            converted = io.BytesIO()
            image.convert("RGB").save(converted, format="PNG")
            width, height = image.size
        return MediaResult(content=converted.getvalue(), mime="image/png", width=width, height=height, model=settings.dashscope_image_model)


class DashScopeVideoProvider:
    def submit(self, image: bytes, duration: float, prompt: str) -> str:
        encoded = base64.b64encode(image).decode("ascii")
        body = _api("POST", "services/aigc/video-generation/video-synthesis", asynchronous=True, payload={
            "model": settings.dashscope_video_model,
            "input": {"prompt": prompt, "img_url": f"data:image/png;base64,{encoded}"},
            "parameters": {"resolution": "720P", "duration": max(2, min(15, round(duration))), "prompt_extend": True, "shot_type": "single", "audio": False},
        })
        return body["output"]["task_id"]

    def get_status(self, remote_job_id: str) -> str:
        body = _api("GET", f"tasks/{remote_job_id}")
        return body["output"]["task_status"]

    def cancel(self, remote_job_id: str) -> None:
        _api("POST", f"tasks/{remote_job_id}/cancel")

    def fetch_result(self, remote_job_id: str) -> MediaResult:
        body = _api("GET", f"tasks/{remote_job_id}")
        if body["output"]["task_status"] != "SUCCEEDED":
            raise ValueError("Video task is not complete")
        data = _download(body["output"]["video_url"])
        return MediaResult(content=data, mime="video/mp4", model=settings.dashscope_video_model)


class DashScopeTTSProvider:
    def synthesize(self, text: str, duration: float) -> MediaResult:
        body = _api("POST", "services/aigc/multimodal-generation/generation", payload={
            "model": settings.dashscope_tts_model,
            "input": {"text": text, "voice": "Cherry", "language_type": "Chinese"},
        })
        audio = _download(body["output"]["audio"]["url"])
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source", Path(directory) / "output.wav"
            source.write_bytes(audio)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-ar", "24000", "-ac", "1", str(output)], check=True, timeout=60)
            data = output.read_bytes()
        return MediaResult(content=data, mime="audio/wav", model=settings.dashscope_tts_model)
