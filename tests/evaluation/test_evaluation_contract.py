from __future__ import annotations

import json
from pathlib import Path

from scripts.evaluate_rag import evaluate


ROOT = Path(__file__).parents[2]


def test_synthetic_evaluation_meets_versioned_baseline() -> None:
    dataset = json.loads((ROOT / "tests/evaluation/dataset_v1.json").read_text())
    baseline = json.loads((ROOT / "tests/evaluation/baseline_v1.json").read_text())
    report = evaluate(dataset)
    for metric, threshold in baseline["thresholds"].items():
        assert report["metrics"][metric] >= threshold


def test_secret_case_has_no_retrieval_candidates() -> None:
    dataset = json.loads((ROOT / "tests/evaluation/dataset_v1.json").read_text())
    report = evaluate(dataset)
    secret = next(item for item in report["cases"] if item["id"] == "secret-process-no-retrieval-context")
    assert secret["selected_step_numbers"] == []
    assert secret["leakage_free"] is True
