import pytest

from app.rag import summary_volume_instruction


@pytest.mark.parametrize(
    ("step_count", "expected"),
    [
        (0, "Processo curto"),
        (15, "Processo curto"),
        (16, "Processo de volume intermediário"),
        (60, "Processo de volume intermediário"),
        (61, "Processo longo"),
        (200, "Processo longo"),
    ],
)
def test_summary_volume_instruction_uses_documented_bands(
    step_count: int,
    expected: str,
) -> None:
    instruction = summary_volume_instruction(step_count)

    assert expected in instruction
    assert "palavras" not in instruction.lower()
    assert "tokens" not in instruction.lower()


def test_summary_volume_instruction_rejects_negative_count() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        summary_volume_instruction(-1)
