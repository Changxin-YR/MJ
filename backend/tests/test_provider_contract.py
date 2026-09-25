from datetime import timedelta

import pytest

from app.agent.inspector import CHECKS
from app.asset.storage import validate
from app.config import settings
from app.generation.service import apply_chinese_image_policy
from app.models import now
from app.providers import dashscope
from app.providers.fake import FakeImageProvider, FakeTTSProvider, FakeVideoProvider
from app.providers.registry import ProviderRegistry


def test_fake_media_providers_obey_contract():
    image = FakeImageProvider().generate("中文分镜：快递员站在雨夜屋顶")
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


def test_image_policy_forces_simplified_chinese_and_preserves_negative_budget():
    prompt, negative = apply_chinese_image_policy("雨夜街道，店铺招牌清晰可见", "低清晰度，" + "x" * 600)
    assert "只能使用简体中文" in prompt
    assert "禁止日文假名" in prompt
    assert "韩文谚文" in prompt
    assert "日文" in negative and "韩文" in negative and "繁体中文" in negative
    assert len(negative) <= 500


def test_dashscope_disables_prompt_rewrite_and_forces_chinese_tts(monkeypatch):
    captured = []

    class CapturedPayload(Exception):
        pass

    def capture(method, path, *, payload=None, asynchronous=False):
        captured.append({"method": method, "path": path, "payload": payload, "asynchronous": asynchronous})
        raise CapturedPayload

    monkeypatch.setattr(dashscope, "_api", capture)

    with pytest.raises(CapturedPayload):
        dashscope.DashScopeImageProvider().generate("简体中文招牌", "日文，韩文")
    image_payload = captured[-1]["payload"]
    assert image_payload["parameters"]["prompt_extend"] is False
    assert "简体中文招牌" in image_payload["input"]["messages"][0]["content"][0]["text"]

    with pytest.raises(CapturedPayload):
        dashscope.DashScopeTTSProvider().synthesize("城市醒来了。", 2)
    tts_payload = captured[-1]["payload"]
    assert tts_payload["input"]["language_type"] == "Chinese"
    assert tts_payload["input"]["text"] == "城市醒来了。"


@pytest.mark.parametrize("text", ["こんにちは，城市。", "안녕하세요，城市。"])
def test_dashscope_tts_rejects_japanese_and_korean_scripts(text):
    with pytest.raises(ValueError, match="Chinese-only"):
        dashscope._validate_chinese_tts_text(text)


def test_media_inspector_checks_visible_text_language():
    assert "visible_text_language" in CHECKS
