from datetime import timedelta

from app.asset.storage import validate
from app.config import settings
from app.models import now
from app.providers.fake import FakeImageProvider, FakeTTSProvider, FakeVideoProvider
from app.providers.registry import ProviderRegistry


def test_fake_media_providers_obey_contract():
    image = FakeImageProvider().generate("Courier on a rooftop")
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
