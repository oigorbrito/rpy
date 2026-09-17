from __future__ import annotations

from typing import Any

import pytest

import app.embeddings_bge as embeddings_bge
from app.embeddings_bge import BGEEmbeddingEncoder


class FakeBGEModel:
    def __init__(self) -> None:
        self.corpus_calls: list[tuple[list[str], dict[str, Any]]] = []
        self.query_calls: list[tuple[list[str], dict[str, Any]]] = []

    def encode_corpus(self, values, **kwargs):
        self.corpus_calls.append((list(values), dict(kwargs)))
        return {"dense_vecs": [[1.0] + [0.0] * 1023 for _ in values]}

    def encode_queries(self, values, **kwargs):
        self.query_calls.append((list(values), dict(kwargs)))
        return {"dense_vecs": [[0.0, 1.0] + [0.0] * 1022 for _ in values]}


@pytest.mark.asyncio
async def test_bge_encoder_is_lazy_and_uses_distinct_query_corpus_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeBGEModel()
    loads: list[dict[str, Any]] = []

    def fake_loader(*, model: str, use_fp16: bool, device: str | None):
        loads.append({"model": model, "use_fp16": use_fp16, "device": device})
        return fake

    monkeypatch.setattr(embeddings_bge, "_load_bge_model", fake_loader)
    encoder = BGEEmbeddingEncoder(use_fp16=False, device="cpu")
    assert loads == []

    documents = await encoder.embed_documents(["movimento um", "movimento dois"])
    query = await encoder.embed_query("sentença")

    assert len(loads) == 1
    assert loads[0] == {"model": "BAAI/bge-m3", "use_fp16": False, "device": "cpu"}
    assert len(documents) == 2
    assert all(len(vector) == 1024 for vector in documents)
    assert len(query) == 1024
    assert fake.corpus_calls == [
        (
            ["movimento um", "movimento dois"],
            {"return_dense": True, "return_sparse": False, "return_colbert_vecs": False},
        )
    ]
    assert fake.query_calls == [
        (
            ["sentença"],
            {"return_dense": True, "return_sparse": False, "return_colbert_vecs": False},
        )
    ]


@pytest.mark.asyncio
async def test_bge_encoder_rejects_empty_inputs() -> None:
    encoder = BGEEmbeddingEncoder()
    assert await encoder.embed_documents([]) == []
    with pytest.raises(ValueError, match="non-empty text"):
        await encoder.embed_documents([""])
    with pytest.raises(ValueError, match="non-empty text"):
        await encoder.embed_query("  ")


@pytest.mark.asyncio
async def test_bge_encoder_rejects_wrong_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadModel:
        def encode_queries(self, values, **kwargs):
            return {"dense_vecs": [[0.0] * 1536]}

    monkeypatch.setattr(
        embeddings_bge,
        "_load_bge_model",
        lambda **kwargs: BadModel(),
    )
    encoder = BGEEmbeddingEncoder()

    with pytest.raises(RuntimeError, match="expected 1024, got 1536"):
        await encoder.embed_query("consulta")


def test_bge_encoder_rejects_unknown_model() -> None:
    with pytest.raises(RuntimeError, match="unsupported BGE embedding model"):
        BGEEmbeddingEncoder(model="other")
