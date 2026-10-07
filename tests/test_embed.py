import math

import httpx
import pytest

from genea.index.embed import EmbeddingError, FakeEmbedder, OllamaEmbedder


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def test_fake_embedder_is_deterministic_and_normalized():
    e = FakeEmbedder(dim=32)
    a, b, c = e.embed(["Jan Novák Brno", "jan novak brno", "zcela jiny text o kocce"])
    assert a == b
    assert math.isclose(_dot(a, a), 1.0, rel_tol=1e-9)
    assert _dot(a, b) > _dot(a, c)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ollama_embedder_success():
    def handler(request):
        assert request.url.path == "/api/embed"
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    e = OllamaEmbedder("http://ollama:11434", "bge-m3", 2, client=_client(handler))
    assert e.embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert e.embed([]) == []


def test_ollama_embedder_http_error():
    e = OllamaEmbedder("http://ollama:11434", "bge-m3", 2,
                       client=_client(lambda r: httpx.Response(404, json={"error": "model not found"})))
    with pytest.raises(EmbeddingError, match="genea doctor"):
        e.embed(["a"])


def test_ollama_embedder_wrong_dimension():
    e = OllamaEmbedder("http://ollama:11434", "bge-m3", 3,
                       client=_client(lambda r: httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})))
    with pytest.raises(EmbeddingError, match="3 dimensions"):
        e.embed(["a"])
