from dataclasses import dataclass
from datetime import datetime, timedelta

import redis

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
    CIRCUIT_TTL_SECONDS = 30
    FAILURE_WINDOW_SECONDS = 120

    def __init__(self):
        real_enabled = bool(settings.dashscope_api_key) and settings.provider_mode in {"dashscope", "auto"}
        # FakeProvider is test/dev-only. Never silently fall back to fake media
        # in auto mode when a real provider is unhealthy.
        fake_enabled = settings.provider_mode in {"fake", "comfyui"}
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

    def _redis(self):
        return redis.Redis.from_url(settings.redis_url, decode_responses=True)

    def _failure_key(self, provider: str) -> str:
        return f"frameforge:provider:{provider}:failures"

    def _open_key(self, provider: str) -> str:
        return f"frameforge:provider:{provider}:open"

    def _shared_is_open(self, provider: str) -> bool | None:
        try:
            return bool(self._redis().exists(self._open_key(provider)))
        except redis.RedisError:
            return None

    def route(self, kind: str) -> ProviderEntry:
        candidates = sorted((e for e in self.entries.values() if e.enabled and kind in e.capabilities), key=lambda e: (e.priority, e.provider))
        for entry in candidates:
            shared_open = self._shared_is_open(entry.provider)
            if shared_open is True:
                entry.circuit = "OPEN"
                entry.health = "UNHEALTHY"
                continue
            if shared_open is False and entry.circuit == "OPEN":
                entry.circuit = "HALF_OPEN"
                entry.health = "DEGRADED"
            elif shared_open is None and entry.circuit == "OPEN" and entry.opened_at and now() - entry.opened_at > timedelta(seconds=self.CIRCUIT_TTL_SECONDS):
                entry.circuit = "HALF_OPEN"
            if entry.circuit != "OPEN":
                return entry
        raise APIError("GENERATION_FAILED", "No healthy provider available", 503)

    def success(self, provider: str):
        entry = self.entries[provider]
        entry.failures = 0
        entry.circuit = "CLOSED"
        entry.health = "HEALTHY"
        entry.opened_at = None
        try:
            self._redis().delete(self._failure_key(provider), self._open_key(provider))
        except redis.RedisError:
            pass

    def failure(self, provider: str):
        entry = self.entries[provider]
        entry.failures += 1
        if entry.failures >= 3:
            entry.circuit = "OPEN"
            entry.opened_at = now()
            entry.health = "UNHEALTHY"
        try:
            client = self._redis()
            failure_key = self._failure_key(provider)
            failures = int(client.incr(failure_key))
            client.expire(failure_key, self.FAILURE_WINDOW_SECONDS)
            if failures >= 3:
                client.set(self._open_key(provider), now().isoformat(), ex=self.CIRCUIT_TTL_SECONDS)
        except redis.RedisError:
            pass


registry = ProviderRegistry()
