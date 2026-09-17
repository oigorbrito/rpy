import asyncio

from scripts.benchmark_reranker import benchmark_cases
from scripts.evaluate_offline_pipeline import load_dataset


def test_synthetic_reranker_benchmark_preserves_mandatory_recall_and_reduces_context() -> None:
    report = asyncio.run(benchmark_cases(load_dataset(), scorer_name="synthetic"))

    assert report["measured_long_cases"] > 0
    assert report["baseline"]["mandatory_milestone_recall"] == 1.0
    assert report["reranked"]["mandatory_milestone_recall"] == 1.0
    assert report["reranked"]["selected_candidates"] <= report["baseline"]["selected_candidates"]
    assert report["reranked"]["policy_candidate_precision"] >= report["baseline"]["policy_candidate_precision"]
    assert report["external_provider_cost_usd"] == 0.0
    assert report["hardware_cost_usd"] is None
    assert report["baseline"]["elapsed_ms"] >= 0.0
    assert report["reranked"]["elapsed_ms"] >= 0.0
