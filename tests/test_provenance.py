from __future__ import annotations

from uuid import uuid4

from app.provenance import selected_movement_sources
from app.retrieval import RankedStep, Step


def test_selected_movement_sources_contains_only_safe_metadata() -> None:
    step = Step(
        id=uuid4(),
        step_number=7,
        text="conteúdo que não pode ser persistido na proveniência",
        title="Decisão",
        source_step_number=99,
    )

    sources = selected_movement_sources([RankedStep(step=step, score=1.0)])

    assert sources == [
        {
            "step_id": step.id,
            "step_number": 7,
            "occurred_at": None,
            "source_order": 0,
        }
    ]
    assert "text" not in sources[0]
    assert "title" not in sources[0]
