from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import app.embeddings as embeddings


class _FakeEmbeddingsAPI:
    def __init__(self, data: list[Any]) -> None:
        self.data = data
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any):
        self.calls.append(kwargs)
        return SimpleNamespace(data=self.data)


class _FakeClient:
    def __init__(self, data: list[Any]) -> None:
        self.embeddings = _FakeEmbeddingsAPI(data)


def _item(index: int, size: int = embeddings.VECTOR_DIMENSIONS):
    return SimpleNamespace(index=index, embedding=[0.0] * size)


@pytest.mark.asyncio
async def test_text_embedding_3_request_is_pinned_to_pgvector_dimension(monkeypatch) -> None:
    fake = _FakeClient([_item(0), _item(1)])
    monkeypatch.setattr(embeddings, "EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setattr(embeddings, "_client", lambda: fake)

    vectors = await embeddings.embed_texts(["primeiro", "segundo"])

    assert len(vectors) == 2
    request = fake.embeddings.calls[0]
    assert request["model"] == "text-embedding-3-large"
    assert request["dimensions"] == 1536
    assert request["input"] == ["primeiro", "segundo"]


@pytest.mark.asyncio
async def test_embedding_dimension_mismatch_fails_before_database_write(monkeypatch) -> None:
    fake = _FakeClient([_item(0, size=3072)])
    monkeypatch.setattr(embeddings, "EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setattr(embeddings, "_client", lambda: fake)

    with pytest.raises(RuntimeError, match="dimension mismatch"):
        await embeddings.embed_texts(["texto"])


@pytest.mark.asyncio
async def test_embedding_response_indexes_must_match_inputs(monkeypatch) -> None:
    fake = _FakeClient([_item(1), _item(2)])
    monkeypatch.setattr(embeddings, "_client", lambda: fake)

    with pytest.raises(RuntimeError, match="indexes"):
        await embeddings.embed_texts(["primeiro", "segundo"])


@pytest.mark.asyncio
async def test_empty_embedding_input_is_rejected_without_provider_call(monkeypatch) -> None:
    fake = _FakeClient([_item(0)])
    monkeypatch.setattr(embeddings, "_client", lambda: fake)

    with pytest.raises(ValueError, match="non-empty"):
        await embeddings.embed_texts(["   "])

    assert fake.embeddings.calls == []
