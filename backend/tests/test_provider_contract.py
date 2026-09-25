from datetime import timedelta
import io
import wave

import pytest

from app.asset.storage import validate
from app.config import settings
from app.generation.service import chinese_visual_negative_prompt, chinese_visual_prompt
from app.models import now
from app.providers import dashscope
from app.providers.fake import FakeImageProvider, FakeTTSProvider, FakeVideoProvider
from app.providers.registry import ProviderRegistry


def test_fake_media_providers_obey_contract():
    image = FakeImageProvider().generate("雨夜屋顶上的中国信使")
    assert validate(image)[:2] == (1280, 720)
    video_provider = FakeVideoProvider()
    task_id = video_provider.submit(image.content, 2, "gentle motion")
    assert video_provider.get_status(task_id) == "SUCCEEDED"
    video = video_provider.fetch_result(task_id)
    assert validate(video)[2] >= 1.9
    voice = FakeTTSProvider().synthesize("Hello", 2)
    assert validate(voice)[2] >= 1.9
    video_provider.cancel(task_id)


def test_provider_circuit_falls_back_and_recovers(monkeypatch):
    monkeypatch.setattr(settings, "provider_mode", "auto")
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "comfyui_checkpoint", "")
    registry = ProviderRegistry()
    assert registry.route("IMAGE").provider == "dashscope"
    for _ in range(3):
        registry.failure("dashscope")
    assert registry.entries["dashscope"].circuit == "OPEN"
    assert registry.route("IMAGE").provider == "fake"
    registry.entries["dashscope"].opened_at = now() - timedelta(seconds=31)
    assert registry.route("IMAGE").provider == "dashscope"
    assert registry.entries["dashscope"].circuit == "HALF_OPEN"
    registry.success("dashscope")
    assert registry.entries["dashscope"].circuit == "CLOSED"


def test_comfyui_mode_routes_images_locally_and_other_media_to_fake(monkeypatch):
    monkeypatch.setattr(settings, "provider_mode", "comfyui")
    monkeypatch.setattr(settings, "comfyui_checkpoint", "approved-model.safetensors")
    registry = ProviderRegistry()
    assert registry.route("IMAGE").provider == "comfyui"
    assert registry.route("VIDEO").provider == "fake"
    assert registry.route("VOICE").provider == "fake"


def test_chinese_visual_guards_are_added_without_dropping_source_prompt():
    prompt = chinese_visual_prompt("雨夜屋顶，角色看向远处的招牌")
    negative = chinese_visual_negative_prompt("多余人物")
    assert prompt.startswith("雨夜屋顶，角色看向远处的招牌")
    assert "只能使用简体中文" in prompt
    assert "禁止日文假名、韩文" in prompt
    assert "日文文字" in negative and "韩文" in negative and "英文单词" in negative


def test_dashscope_chinese_media_controls(monkeypatch):
    seen = []

    def fake_api(method, path, *, payload=None, asynchronous=False):
        seen.append((method, path, payload, asynchronous))
        if "video-generation" in path:
            return {"output": {"task_id": "task-1"}}
        if payload["model"] == settings.dashscope_tts_model:
            return {"output": {"audio": {"url": "https://example.aliyuncs.com/audio.wav"}}}
        return {"output": {"choices": [{"message": {"content": [{"image": "https://example.aliyuncs.com/image.png"}]}}]}}

    image_bytes = io.BytesIO()
    from PIL import Image
    Image.new("RGB", (16, 16), "white").save(image_bytes, format="PNG")

    audio_bytes = io.BytesIO()
    with wave.open(audio_bytes, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 2400)

    def fake_download(url):
        return image_bytes.getvalue() if url.endswith(".png") else audio_bytes.getvalue()

    monkeypatch.setattr(dashscope, "_api", fake_api)
    monkeypatch.setattr(dashscope, "_download", fake_download)

    dashscope.DashScopeImageProvider().generate("纯中文场景", "禁止外语文字")
    dashscope.DashScopeVideoProvider().submit(image_bytes.getvalue(), 3, "角色向前走")
    dashscope.DashScopeTTSProvider().synthesize("城市醒来了。", 1)

    image_payload = seen[0][2]
    video_payload = seen[1][2]
    tts_payload = seen[2][2]
    assert image_payload["parameters"]["prompt_extend"] is False
    assert video_payload["parameters"]["prompt_extend"] is False
    assert tts_payload["input"]["language_type"] == "Chinese"


@pytest.mark.parametrize("text", ["こんにちは，城市。", "안녕하세요，城市。"])
def test_dashscope_tts_rejects_japanese_and_korean_scripts(text):
    with pytest.raises(ValueError, match="Chinese-only"):
        dashscope._validate_chinese_tts_text(text)
