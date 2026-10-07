"""Text embedders: Ollama for real use, a deterministic fake for tests."""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

import httpx

from genea.config import Settings
from genea.text import normalize


class EmbeddingError(RuntimeError):
    """Embedding failed (Ollama down, model missing, unexpected output)."""


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    def __init__(self, url: str, model: str, dim: int, timeout: float = 120.0,
                 client: httpx.Client | None = None) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.dim = dim
        self.timeout = timeout
        self.client = client or httpx.Client()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self.client.post(f"{self.url}/api/embed",
                                        json={"model": self.model, "input": texts},
                                        timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingError(
                f"Ollama embedding failed ({exc}). Is Ollama running and `{self.model}` pulled? "
                "Run `genea doctor`."
            ) from exc
        vectors = response.json().get("embeddings", [])
        if len(vectors) != len(texts) or any(len(v) != self.dim for v in vectors):
            raise EmbeddingError(
                f"{self.model} returned unexpected embeddings; expected {self.dim} dimensions. "
                "Check embed_model/embed_dim in config.toml."
            )
        return vectors


class FakeEmbedder:
    """Hashed bag-of-words vectors: deterministic, offline, good enough for tests."""

    model = "fake"

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            vector = [0.0] * self.dim
            for token in normalize(text).split():
                vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in vector)) or 1.0
            result.append([x / norm for x in vector])
        return result


def make_embedder(settings: Settings) -> OllamaEmbedder:
    return OllamaEmbedder(settings.ollama_url, settings.embed_model, settings.embed_dim)
