from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MediaResult:
    content: bytes
    mime: str
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    model: str = ""
    cost: float = 0


class LLMProvider(Protocol):
    def complete(self, prompt: str) -> str: ...


class ImageProvider(Protocol):
    def generate(self, prompt: str, negative_prompt: str = "") -> MediaResult: ...


class VideoProvider(Protocol):
    def submit(self, image: bytes, duration: float, prompt: str) -> str: ...
    def get_status(self, remote_job_id: str) -> str: ...
    def cancel(self, remote_job_id: str) -> None: ...
    def fetch_result(self, remote_job_id: str) -> MediaResult: ...


class TTSProvider(Protocol):
    def synthesize(self, text: str, duration: float) -> MediaResult: ...


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...
