from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from app.reranker_bge import BGERerankerScorer
from app.reranking import select_context_steps
from app.retrieval import Step
from scripts.evaluate_offline_pipeline import (
    DEFAULT_DATASET,
    _milestone_text,
    _synthetic_steps,
    load_dataset,
)


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Rpy retrieval with and without reranking")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--scorer", choices=("synthetic", "bge"), default="synthetic")
    args = parser.parse_args()
    report = asyncio.run(benchmark_cases(load_dataset(args.dataset), scorer_name=args.scorer))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
