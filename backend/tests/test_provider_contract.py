import pytest

from app.agent.inspector import CHECKS, _frames
from app.asset.storage import validate
from app.config import settings
from app.generation.service import apply_chinese_image_policy
from app.providers import dashscope, embeddings
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
    sampled = _frames(video)
    assert len(sampled) == 4
    assert all(frame.startswith(b"\xff\xd8") for frame in sampled)
    voice = FakeTTSProvider().synthesize("Hello", 2)
    assert validate(voice)[2] >= 1.9
    video_provider.cancel(task_id)


def test_provider_circuit_falls_back_across_registry_instances(monkeypatch):
    monkeypatch.setattr(settings, "provider_mode", "auto")
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "comfyui_checkpoint", "")
    worker_registry = ProviderRegistry()
    api_registry = ProviderRegistry()
    worker_registry.success("dashscope")
    try:
        assert api_registry.route("IMAGE").provider == "dashscope"
        for _ in range(3):
            worker_registry.failure("dashscope")
        assert worker_registry.entries["dashscope"].circuit == "OPEN"
        assert api_registry.route("IMAGE").provider == "fake"
        worker_registry._redis().delete(worker_registry._open_key("dashscope"))
        assert api_registry.route("IMAGE").provider == "dashscope"
    finally:
        worker_registry.success("dashscope")
    assert worker_registry.entries["dashscope"].circuit == "CLOSED"


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
        dashscope.DashScopeVideoProvider().submit(b"image", 3, "只允许简体中文可见文字")
    video_payload = captured[-1]["payload"]
    assert video_payload["parameters"]["prompt_extend"] is False

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



def test_dashscope_embedding_batches_texts_and_preserves_order(monkeypatch):
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    captured = []

    class Response:
        def __init__(self, rows):
            self.rows = rows

        def raise_for_status(self):
            return None

        def json(self):
            return {"output": {"embeddings": self.rows}}

    def fake_post(url, *, headers, json, timeout):
        captured.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        rows = [
            {"text_index": index, "embedding": [float(index + 1)] * 1024}
            for index, _ in enumerate(json["input"]["texts"])
        ]
        return Response(list(reversed(rows)))

    monkeypatch.setattr(embeddings.httpx, "post", fake_post)
    provider = embeddings.DashScopeEmbeddingProvider()
    vectors = provider.embed_many(["第一段中文", "第二段中文"], text_type="document")
    assert captured[-1]["url"].endswith("/services/embeddings/text-embedding/text-embedding")
    assert captured[-1]["json"]["input"]["texts"] == ["第一段中文", "第二段中文"]
    assert captured[-1]["json"]["parameters"]["dimension"] == 1024
    assert captured[-1]["json"]["parameters"]["text_type"] == "document"
    assert "instruct" not in captured[-1]["json"]["parameters"]
    assert len(vectors) == 2
    assert vectors[0][0] == 1.0
    assert vectors[1][0] == 2.0

    query_vector = provider.embed("顾七为什么要进石门？", text_type="query")
    assert query_vector[0] == 1.0
    assert captured[-1]["json"]["parameters"]["text_type"] == "query"
    assert "Chinese fiction" in captured[-1]["json"]["parameters"]["instruct"]
