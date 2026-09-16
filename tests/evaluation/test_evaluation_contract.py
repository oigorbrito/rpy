from __future__ import annotations

import json
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[2]
_SPEC = importlib.util.spec_from_file_location("evaluate_rag", ROOT / "scripts/evaluate_rag.py")
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
evaluate = _MODULE.evaluate


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
