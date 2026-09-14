from uuid import uuid4

from app.retrieval import Step, bm25_scores, rank_steps


def _step(number: int, text: str) -> Step:
    return Step(id=uuid4(), step_number=number, text=text)


def test_bm25_prefers_exact_terms() -> None:
    relevant = _step(1, "CITAÇÃO do requerido realizada")
    other = _step(2, "Petição juntada aos autos")
    scores = bm25_scores("citação requerido", [relevant, other])
    assert scores[relevant.id] > scores[other.id]


def test_short_process_returns_every_step() -> None:
    steps = [_step(i, f"movimento {i}") for i in range(1, 21)]
    ranked = rank_steps(query="sentença", steps=steps, limit=5)
    assert [item.step.step_number for item in ranked] == list(range(1, 21))


def test_long_process_forces_first_last_recent_and_milestone() -> None:
    steps = [_step(i, f"movimento comum {i}") for i in range(1, 51)]
    steps[9] = _step(10, "SENTENÇA julgando o mérito")
    ranked = rank_steps(query="termo inexistente", steps=steps, limit=8)
    numbers = {item.step.step_number for item in ranked}
    assert 1 in numbers
    assert 50 in numbers
    assert {46, 47, 48, 49, 50}.issubset(numbers)
    assert 10 in numbers


def test_recency_boost_favors_later_equal_hits() -> None:
    steps = [_step(i, "sem relação") for i in range(1, 41)]
    early = _step(42, "penhora realizada")
    late = _step(50, "penhora realizada")
    steps.extend([early, late])
    ranked = rank_steps(query="penhora", steps=steps, limit=10)
    scores = {item.step.id: item.score for item in ranked}
    assert scores[late.id] > scores[early.id]
