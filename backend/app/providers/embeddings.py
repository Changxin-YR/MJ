"""Embedding providers. Collections are separated by model and vector size."""

import hashlib
import math
import re

import httpx

from app.config import settings


def lexical_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for part in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) == 1:
                tokens.append(part)
            else:
                tokens.extend(part[i:i + 2] for i in range(len(part) - 1))
                if len(part) >= 3:
                    tokens.extend(part[i:i + 3] for i in range(len(part) - 2))
        else:
            tokens.append(part)
    return tokens


class FakeEmbeddingProvider:
    model = "deterministic-test-v2-cn-ngram"
    dimensions = 128
    batch_size = 100

    def embed(self, text: str) -> list[float]:
        values = [0.0] * self.dimensions
        for token in lexical_tokens(text):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:2], "big") % self.dimensions
            values[index] += 1 if digest[2] % 2 else -1
        length = math.sqrt(sum(v * v for v in values)) or 1
        return [v / length for v in values]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class DashScopeEmbeddingProvider:
    dimensions = 1024
    batch_size = 10

    @property
    def model(self) -> str:
        return settings.dashscope_embedding_model

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) > self.batch_size:
            raise ValueError(f"Embedding batch exceeds {self.batch_size} texts")
        if not settings.dashscope_api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required for real embeddings")
        response = httpx.post(
            f"{settings.dashscope_chat_base_url.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            json={"model": self.model, "input": texts, "dimensions": self.dimensions, "encoding_format": "float"},
            timeout=60,
        )
        response.raise_for_status()
        rows = sorted(response.json()["data"], key=lambda row: row.get("index", 0))
        if len(rows) != len(texts):
            raise ValueError("Embedding provider returned an unexpected batch size")
        vectors = []
        for row in rows:
            vector = row["embedding"]
            if len(vector) != self.dimensions:
                raise ValueError("Embedding provider returned an unexpected vector dimension")
            vectors.append([float(value) for value in vector])
        return vectors

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]


def selected_embedding_provider():
    if settings.embedding_mode == "fake":
        return FakeEmbeddingProvider()
    if settings.embedding_mode == "dashscope":
        return DashScopeEmbeddingProvider()
    raise RuntimeError(f"Unsupported embedding mode: {settings.embedding_mode}")
