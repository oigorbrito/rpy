from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

_BENCHMARK_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_reranker.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_reranker", _BENCHMARK_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_BENCHMARK = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BENCHMARK)


def test_synthetic_reranker_benchmark_preserves_mandatory_recall_and_reduces_context() -> None:
    report = asyncio.run(
        _BENCHMARK.benchmark_cases(_BENCHMARK.load_dataset(), scorer_name="synthetic")
    )

    assert report["measured_long_cases"] > 0
    assert report["baseline"]["mandatory_milestone_recall"] == 1.0
    assert report["reranked"]["mandatory_milestone_recall"] == 1.0
    assert report["reranked"]["selected_candidates"] <= report["baseline"]["selected_candidates"]
    assert report["reranked"]["policy_candidate_precision"] >= report["baseline"]["policy_candidate_precision"]
    assert report["external_provider_cost_usd"] == 0.0
    assert report["hardware_cost_usd"] is None
    assert report["baseline"]["elapsed_ms"] >= 0.0
    assert report["reranked"]["elapsed_ms"] >= 0.0


def test_synthetic_benchmark_contract_reports_no_failures_for_current_fixture() -> None:
    report = asyncio.run(
        _BENCHMARK.benchmark_cases(_BENCHMARK.load_dataset(), scorer_name="synthetic")
    )

    failures = _BENCHMARK.contract_failures(report)

    assert failures == []


def test_synthetic_benchmark_contract_detects_precision_regression() -> None:
    report = {
        "measured_long_cases": 1,
        "baseline": {
            "mandatory_milestone_recall": 1.0,
            "policy_candidate_precision": 0.5,
            "selected_candidates": 20,
        },
        "reranked": {
            "mandatory_milestone_recall": 1.0,
            "policy_candidate_precision": 0.4,
            "selected_candidates": 15,
        },
    }

    failures = _BENCHMARK.contract_failures(report)

    assert "reranked synthetic precision must not regress below baseline" in failures
