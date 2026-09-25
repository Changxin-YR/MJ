"""Embedding providers. Collections are separated by model and vector size."""

import hashlib
import math
import re

import httpx

from app.config import settings


class FakeEmbeddingProvider:
    model = "deterministic-test-v1"
    dimensions = 64

    def embed(self, text: str) -> list[float]:
        values = [0.0] * self.dimensions
        for token in re.findall(r"[\w\u4e00-\u9fff]+", text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:2], "big") % self.dimensions
            values[index] += 1 if digest[2] % 2 else -1
        length = math.sqrt(sum(v * v for v in values)) or 1
        return [v / length for v in values]


class DashScopeEmbeddingProvider:
    dimensions = 1024

    @property
    def model(self) -> str:
        return settings.dashscope_embedding_model

    def embed(self, text: str) -> list[float]:
        if not settings.dashscope_api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required for real embeddings")
        response = httpx.post(
            f"{settings.dashscope_chat_base_url.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            json={"model": self.model, "input": text, "dimensions": self.dimensions, "encoding_format": "float"},
            timeout=60,
        )
        response.raise_for_status()
        vector = response.json()["data"][0]["embedding"]
        if len(vector) != self.dimensions:
            raise ValueError("Embedding provider returned an unexpected vector dimension")
        return [float(value) for value in vector]


def selected_embedding_provider():
    if settings.embedding_mode == "fake":
        return FakeEmbeddingProvider()
    if settings.embedding_mode == "dashscope":
        return DashScopeEmbeddingProvider()
    raise RuntimeError(f"Unsupported embedding mode: {settings.embedding_mode}")
