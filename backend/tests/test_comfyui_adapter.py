import io
import json

import httpx
import pytest
from PIL import Image

from app.config import settings
from app.providers import comfyui


def test_fixed_comfyui_workflow_and_private_media_fetch(monkeypatch):
    monkeypatch.setattr(settings, "comfyui_url", "http://comfyui:8188")
    monkeypatch.setattr(settings, "comfyui_checkpoint", "approved-model.safetensors")
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(image, format="PNG")
    seen = {}
    prompt_id = "27a1192c-8f16-42c5-8acc-ecad1dc12940"

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"prompt_id": prompt_id})
        if request.url.path == f"/history/{prompt_id}":
            return httpx.Response(200, json={prompt_id: {"outputs": {"7": {"images": [{"filename": "frameforge_00001_.png", "subfolder": "", "type": "output"}]}}}})
        if request.url.path == "/view":
            return httpx.Response(200, content=image.getvalue())
        raise AssertionError(f"Unexpected ComfyUI request: {request.url}")

    monkeypatch.setattr(comfyui, "client_factory", lambda: httpx.Client(base_url=settings.comfyui_url, transport=httpx.MockTransport(respond)))
    result = comfyui.ComfyUIImageProvider().generate("one courier", "extra fingers")
    assert result.content == image.getvalue()
    workflow = seen["body"]["prompt"]
    assert {node["class_type"] for node in workflow.values()} == {"CheckpointLoaderSimple", "EmptyLatentImage", "CLIPTextEncode", "KSampler", "VAEDecode", "SaveImage"}
    assert workflow["1"]["inputs"]["ckpt_name"] == "approved-model.safetensors"
    assert workflow["3"]["inputs"]["text"] == "one courier"
    assert workflow["4"]["inputs"]["text"] == "extra fingers"


def test_comfyui_rejects_workflow_escape_and_external_host(monkeypatch):
    with pytest.raises(ValueError):
        comfyui.controlled_workflow("one courier", "", "../model.safetensors", 1)
    monkeypatch.setattr(settings, "comfyui_url", "http://evil.example:8188")
    monkeypatch.setattr(settings, "comfyui_checkpoint", "approved-model.safetensors")
    with pytest.raises(ValueError):
        comfyui.ComfyUIImageProvider().generate("one courier")


def test_comfyui_accepts_only_private_local_bridge(monkeypatch):
    monkeypatch.setattr(settings, "comfyui_url", "http://host.docker.internal:8189")
    monkeypatch.setattr(settings, "comfyui_checkpoint", "approved-model.safetensors")
    monkeypatch.setattr(comfyui, "client_factory", lambda: (_ for _ in ()).throw(RuntimeError("private bridge reached")))
    with pytest.raises(RuntimeError, match="private bridge reached"):
        comfyui.ComfyUIImageProvider().generate("courier")
