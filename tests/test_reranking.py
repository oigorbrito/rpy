from uuid import uuid4

from app.retrieval import Step, rerank_steps


def test_reranker_limits_candidates_and_preserves_mandatory_movements() -> None:
    steps = [Step(id=uuid4(), step_number=i, text=f"movimento {i}") for i in range(1, 51)]
    steps[9] = Step(id=steps[9].id, step_number=10, text="SENTENÇA de mérito")
    scores = {step.id: float(100 - step.step_number) for step in steps}

    selected = rerank_steps(steps=steps, scores=scores, limit=15)

    numbers = [step.step_number for step in selected]
    assert len(selected) == 15
    assert 1 in numbers
    assert 10 in numbers
    assert {46, 47, 48, 49, 50}.issubset(numbers)


def test_reranker_is_deterministic_for_equal_scores() -> None:
    steps = [Step(id=uuid4(), step_number=i, text="movimento") for i in range(1, 21)]
    scores = {step.id: 1.0 for step in steps}

    first = [step.id for step in rerank_steps(steps=steps, scores=scores, limit=8)]
    second = [step.id for step in rerank_steps(steps=steps, scores=scores, limit=8)]

    assert first == second
