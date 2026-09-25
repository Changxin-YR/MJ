from dataclasses import dataclass
from datetime import datetime, timedelta

from app.api.errors import APIError
from app.config import settings
from app.models import now
from app.providers.comfyui import ComfyUIImageProvider
from app.providers.dashscope import (
    DashScopeImageProvider,
    DashScopeTTSProvider,
    DashScopeVideoProvider,
)
from app.providers.fake import FakeImageProvider, FakeTTSProvider, FakeVideoProvider


@dataclass
class ProviderEntry:
    provider: str
    enabled: bool
    capabilities: set[str]
    priority: int
    cost: dict[str, float]
    health: str = "HEALTHY"
    failures: int = 0
    circuit: str = "CLOSED"
    opened_at: datetime | None = None


class ProviderRegistry:
    def __init__(self):
        real_enabled = bool(settings.dashscope_api_key) and settings.provider_mode in {"dashscope", "auto"}
        fake_enabled = settings.provider_mode in {"fake", "auto", "comfyui"}
        comfy_enabled = bool(settings.comfyui_checkpoint) and settings.provider_mode in {"comfyui", "auto"}
        self.entries = {
            "fake": ProviderEntry("fake", fake_enabled, {"IMAGE", "VIDEO", "VOICE"}, 100, {"IMAGE": 0.0, "VIDEO": 0.0, "VOICE": 0.0}),
            "dashscope": ProviderEntry("dashscope", real_enabled, {"IMAGE", "VIDEO", "VOICE"}, 10, {"IMAGE": 0.20, "VIDEO": 0.15, "VOICE": 0.05}),
            "comfyui": ProviderEntry("comfyui", comfy_enabled, {"IMAGE"}, 5, {"IMAGE": 0.20}),
        }
        self.adapters = {
            "fake": {"IMAGE": FakeImageProvider(), "VIDEO": FakeVideoProvider(), "VOICE": FakeTTSProvider()},
            "dashscope": {"IMAGE": DashScopeImageProvider(), "VIDEO": DashScopeVideoProvider(), "VOICE": DashScopeTTSProvider()},
            "comfyui": {"IMAGE": ComfyUIImageProvider()},
        }

    def adapter(self, provider: str, kind: str):
        return self.adapters[provider][kind]

    def route(self, kind: str) -> ProviderEntry:
        candidates = sorted((e for e in self.entries.values() if e.enabled and kind in e.capabilities), key=lambda e: (e.priority, e.provider))
        for entry in candidates:
            if entry.circuit == "OPEN" and entry.opened_at and now() - entry.opened_at > timedelta(seconds=30):
                entry.circuit = "HALF_OPEN"
            if entry.circuit != "OPEN":
                return entry
        raise APIError("GENERATION_FAILED", "No healthy provider available", 503)

    def success(self, provider: str):
        entry = self.entries[provider]
        entry.failures = 0
        entry.circuit = "CLOSED"
        entry.health = "HEALTHY"

    def failure(self, provider: str):
        entry = self.entries[provider]
        entry.failures += 1
        if entry.failures >= 3:
            entry.circuit = "OPEN"
            entry.opened_at = now()
            entry.health = "UNHEALTHY"


registry = ProviderRegistry()
