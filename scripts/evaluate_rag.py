from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.retrieval import Step, rank_steps


def _steps(case: dict[str, Any]) -> list[Step]:
    return [
        Step(id=number, step_number=number, title=item.get("title"), text=str(item.get("text") or ""))
        for number, item in enumerate(case["process"]["steps"], start=1)
    ]


def evaluate(dataset: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    required_total = required_hit_total = relevant_total = relevant_hit_total = 0
    selected_total = source_hits = 0
    leakage_free = True

    for case in dataset["cases"]:
        expected = case["expected"]
        secret = int(case["process"].get("secrecy_level") or 0) > 0
        steps = [] if secret else _steps(case)
        vector_scores = {int(key): float(value) for key, value in case.get("vector_scores", {}).items()}
        ranked = rank_steps(
            query=case["query"],
            steps=steps,
            vector_scores={step.id: vector_scores.get(step.step_number, 0.0) for step in steps},
            limit=20,
        )
        selected = [item.step.step_number for item in ranked]
        selected_set = set(selected)
        required = set(expected["required_step_numbers"])
        relevant = set(expected["relevant_step_numbers"])
        required_hit = len(required & selected_set)
        relevant_hit = len(relevant & selected_set)
        leaked = [sentinel for sentinel in expected.get("must_not_leak", []) if any(sentinel in step.searchable_text for step in steps)]
        case_leakage_free = not leaked
        leakage_free = leakage_free and case_leakage_free
        source_present = bool(selected)
        source_hits += source_present == bool(expected["sources_expected"])
        required_total += len(required)
        required_hit_total += required_hit
        relevant_total += len(relevant)
        relevant_hit_total += relevant_hit
        selected_total += len(selected_set)
        results.append({
            "id": case["id"],
            "selected_step_numbers": selected,
            "required_recall": required_hit / len(required) if required else 1.0,
            "candidate_precision": relevant_hit / len(selected_set) if selected_set else 1.0,
            "source_present": source_present,
            "leakage_free": case_leakage_free,
            "leaked": leaked,
        })

    return {
        "dataset": dataset["version"],
        "configuration": {"retrieval": "app.retrieval.rank_steps", "provider": "none"},
        "metrics": {
            "required_movement_recall": required_hit_total / required_total if required_total else 1.0,
            "candidate_precision": relevant_hit_total / selected_total if selected_total else 1.0,
            "source_presence": source_hits / len(results) if results else 1.0,
            "leakage_free": 1.0 if leakage_free else 0.0,
        },
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    report = evaluate(json.loads(args.dataset.read_text(encoding="utf-8")))
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        for metric, threshold in baseline["thresholds"].items():
            actual = report["metrics"].get(metric)
            if actual is None or actual < threshold:
                print(f"REGRESSION {metric}: {actual} < {threshold}", file=sys.stderr)
                return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
