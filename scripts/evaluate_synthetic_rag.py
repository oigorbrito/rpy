from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_DATASET = Path("tests/eval/synthetic_cases.json")


def _as_set(case: dict[str, Any], key: str) -> set[str]:
    value = case.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{case.get('case_id', '<unknown>')}: {key} must be a list of strings")
    return set(value)


def evaluate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not cases:
        raise ValueError("synthetic evaluation dataset must not be empty")

    claim_total = 0
    unsupported_claims = 0
    expected_milestones = 0
    recovered_milestones = 0
    expected_inconsistencies = 0
    detected_inconsistencies = 0
    predicted_attention = 0
    false_positive_attention = 0
    domains: set[str] = set()
    special_cases: set[str] = set()

    for case in cases:
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("every case must have a non-empty case_id")

        domain = case.get("domain")
        if not isinstance(domain, str) or not domain:
            raise ValueError(f"{case_id}: domain must be a non-empty string")
        domains.add(domain)

        tags = _as_set(case, "tags")
        special_cases.update(tags)

        claims = case.get("claims", [])
        if not isinstance(claims, list):
            raise ValueError(f"{case_id}: claims must be a list")
        for claim in claims:
            if not isinstance(claim, dict) or not isinstance(claim.get("text"), str):
                raise ValueError(f"{case_id}: every claim must contain text")
            supported = claim.get("supported")
            if not isinstance(supported, bool):
                raise ValueError(f"{case_id}: every claim must contain boolean supported")
            claim_total += 1
            unsupported_claims += int(not supported)

        expected = _as_set(case, "expected_milestones")
        predicted = _as_set(case, "predicted_milestones")
        expected_milestones += len(expected)
        recovered_milestones += len(expected & predicted)

        expected_issues = _as_set(case, "expected_inconsistencies")
        predicted_issues = _as_set(case, "predicted_inconsistencies")
        expected_inconsistencies += len(expected_issues)
        detected_inconsistencies += len(expected_issues & predicted_issues)

        expected_attention = _as_set(case, "expected_attention")
        predicted_attention_items = _as_set(case, "predicted_attention")
        predicted_attention += len(predicted_attention_items)
        false_positive_attention += len(predicted_attention_items - expected_attention)

    return {
        "cases": len(cases),
        "coverage": {
            "domains": sorted(domains),
            "tags": sorted(special_cases),
        },
        "metrics": {
            "unsupported_assertion_rate": (
                unsupported_claims / claim_total if claim_total else 0.0
            ),
            "milestone_recall": (
                recovered_milestones / expected_milestones if expected_milestones else 1.0
            ),
            "inconsistency_detection_recall": (
                detected_inconsistencies / expected_inconsistencies
                if expected_inconsistencies
                else 1.0
            ),
            "attention_false_positive_rate": (
                false_positive_attention / predicted_attention if predicted_attention else 0.0
            ),
        },
        "counts": {
            "claims": claim_total,
            "unsupported_claims": unsupported_claims,
            "expected_milestones": expected_milestones,
            "recovered_milestones": recovered_milestones,
            "expected_inconsistencies": expected_inconsistencies,
            "detected_inconsistencies": detected_inconsistencies,
            "predicted_attention": predicted_attention,
            "false_positive_attention": false_positive_attention,
        },
    }


def load_dataset(path: Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("synthetic evaluation dataset must be a JSON array")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Score the provider-free synthetic RAG dataset")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = evaluate(load_dataset(args.dataset))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
