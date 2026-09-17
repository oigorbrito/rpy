from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.reranker_bge as bge
from app.retrieval import Step


class _FakeReranker:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def compute_score(self, pairs, *, normalize):
        self.calls.append((pairs, normalize))
        return self.scores


@pytest.mark.asyncio
async def test_bge_scorer_uses_query_passage_pairs_and_normalized_scores(monkeypatch) -> None:
    fake = _FakeReranker([0.2, 0.9])
    loads = []

    def fake_load(*, model, use_fp16):
        loads.append((model, use_fp16))
        return fake

    monkeypatch.setattr(bge, "_load_flag_reranker", fake_load)
    scorer = bge.BGERerankerScorer(model="local/model", use_fp16=True)
    steps = [
        Step(id=uuid4(), step_number=1, title="Decisão", text="Tutela deferida."),
        Step(id=uuid4(), step_number=2, text="Audiência designada."),
    ]

    scores = await scorer("situação atual", steps)
    again = await scorer("situação atual", steps[:1])

    assert loads == [("local/model", True)]
    assert fake.calls[0] == (
        [
            ["situação atual", "Decisão Tutela deferida."],
            ["situação atual", "Audiência designada."],
        ],
        True,
    )
    assert scores == {steps[0].id: 0.2, steps[1].id: 0.9}
    assert again == {steps[0].id: 0.2}


@pytest.mark.asyncio
async def test_empty_candidate_list_does_not_load_model(monkeypatch) -> None:
    monkeypatch.setattr(
        bge,
        "_load_flag_reranker",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not load model")),
    )
    scorer = bge.BGERerankerScorer(model="local/model")

    assert await scorer("query", []) == {}


def test_invalid_boolean_configuration_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("RERANKER_USE_FP16", "maybe")
    with pytest.raises(RuntimeError, match="must be a boolean"):
        bge.BGERerankerScorer(model="local/model")


def test_score_count_mismatch_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="1 scores for 2 candidates"):
        bge._coerce_scores([0.5], expected=2)


def test_non_finite_score_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="non-finite"):
        bge._coerce_scores([float("nan")], expected=1)


def test_missing_optional_dependency_has_actionable_error(monkeypatch) -> None:
    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "FlagEmbedding":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with pytest.raises(RuntimeError, match=r"rpy\[reranker\]"):
        bge._load_flag_reranker(model="BAAI/bge-reranker-v2-m3", use_fp16=False)
