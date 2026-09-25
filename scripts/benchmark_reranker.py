from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from app.reranker_bge import BGERerankerScorer
from app.reranking import select_context_steps
from app.retrieval import Step

_EVALUATOR_PATH = Path(__file__).with_name("evaluate_offline_pipeline.py")
_SPEC = importlib.util.spec_from_file_location("evaluate_offline_pipeline_for_reranker", _EVALUATOR_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_EVALUATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EVALUATOR)
DEFAULT_DATASET = _EVALUATOR.DEFAULT_DATASET


def load_dataset(path: Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    return _EVALUATOR.load_dataset(path)


def _synthetic_steps(case: dict[str, Any]):
    return _EVALUATOR._synthetic_steps(case)


def _milestone_text(label: str) -> str:
    return _EVALUATOR._milestone_text(label)


def _synthetic_scorer(query: str, steps: list[Step]) -> dict[Any, float]:
    terms = {term.casefold() for term in query.split() if term.strip()}
    scores: dict[Any, float] = {}
    for step in steps:
        haystack = step.searchable_text.casefold()
        scores[step.id] = float(sum(1 for term in terms if term in haystack))
    return scores


async def _score_synthetic(query: str, steps: list[Step]) -> dict[Any, float]:
    return _synthetic_scorer(query, steps)


async def benchmark_cases(
    cases: list[dict[str, Any]], *, scorer_name: str = "synthetic"
) -> dict[str, Any]:
    if scorer_name == "synthetic":
        scorer = _score_synthetic
    elif scorer_name == "bge":
        scorer = BGERerankerScorer()
    else:
        raise ValueError(f"unsupported scorer: {scorer_name}")

    baseline_selected = 0
    reranked_selected = 0
    baseline_relevant = 0
    reranked_relevant = 0
    baseline_milestones = 0
    reranked_milestones = 0
    milestone_expected = 0
    measured_cases = 0
    baseline_ms = 0.0
    reranked_ms = 0.0

    for case in cases:
        if int(case.get("secrecy_level", 0)) > 0:
            continue
        steps, policy_relevant, milestone_numbers = _synthetic_steps(case)
        if len(steps) <= 40:
            continue
        query = _milestone_text(str(case["expected_milestones"][0]))

        started = perf_counter()
        baseline = await select_context_steps(query=query, steps=steps)
        baseline_ms += (perf_counter() - started) * 1000

        started = perf_counter()
        reranked = await select_context_steps(query=query, steps=steps, scorer=scorer)
        reranked_ms += (perf_counter() - started) * 1000

        baseline_numbers = {item.step.step_number for item in baseline}
        reranked_numbers = {item.step.step_number for item in reranked}
        baseline_selected += len(baseline_numbers)
        reranked_selected += len(reranked_numbers)
        baseline_relevant += len(baseline_numbers & policy_relevant)
        reranked_relevant += len(reranked_numbers & policy_relevant)
        baseline_milestones += len(baseline_numbers & milestone_numbers)
        reranked_milestones += len(reranked_numbers & milestone_numbers)
        milestone_expected += len(milestone_numbers)
        measured_cases += 1

    def precision(relevant: int, selected: int) -> float:
        return relevant / selected if selected else 1.0

    def recall(recovered: int) -> float:
        return recovered / milestone_expected if milestone_expected else 1.0

    return {
        "scorer": scorer_name,
        "measured_long_cases": measured_cases,
        "baseline": {
            "mandatory_milestone_recall": recall(baseline_milestones),
            "policy_candidate_precision": precision(baseline_relevant, baseline_selected),
            "selected_candidates": baseline_selected,
            "elapsed_ms": round(baseline_ms, 3),
        },
        "reranked": {
            "mandatory_milestone_recall": recall(reranked_milestones),
            "policy_candidate_precision": precision(reranked_relevant, reranked_selected),
            "selected_candidates": reranked_selected,
            "elapsed_ms": round(reranked_ms, 3),
        },
        "external_provider_cost_usd": 0.0,
        "hardware_cost_usd": None,
    }


def contract_failures(report: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if int(report.get("measured_long_cases", 0)) <= 0:
        failures.append("benchmark dataset has no measured long cases")

    baseline = report.get("baseline")
    reranked = report.get("reranked")
    if not isinstance(baseline, dict) or not isinstance(reranked, dict):
        return failures + ["benchmark report is missing baseline/reranked metrics"]

    if float(baseline.get("mandatory_milestone_recall", 0.0)) != 1.0:
        failures.append("baseline mandatory milestone recall must remain 1.0")
    if float(reranked.get("mandatory_milestone_recall", 0.0)) != 1.0:
        failures.append("reranked mandatory milestone recall must remain 1.0")
    if int(reranked.get("selected_candidates", 0)) > int(
        baseline.get("selected_candidates", 0)
    ):
        failures.append("reranked context must not exceed baseline selected candidates")
    if float(reranked.get("policy_candidate_precision", 0.0)) < float(
        baseline.get("policy_candidate_precision", 0.0)
    ):
        failures.append("reranked synthetic precision must not regress below baseline")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Rpy retrieval with and without reranking")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--scorer", choices=("synthetic", "bge"), default="synthetic")
    parser.add_argument(
        "--check-contract",
        action="store_true",
        help="fail if the existing deterministic benchmark contract regresses",
    )
    args = parser.parse_args()
    report = asyncio.run(benchmark_cases(load_dataset(args.dataset), scorer_name=args.scorer))
    failures = contract_failures(report) if args.check_contract else []
    if args.check_contract:
        report["contract_check"] = {
            "passed": not failures,
            "failure_count": len(failures),
            "failures": failures,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
