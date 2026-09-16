from app.validation import validar

CODE = "0000000-00.0000.0.00.0000"
PARTIES = [{"name": "Maria da Silva"}]
STEPS = [
    {"step_number": 1, "occurred_at": "2026-09-14T12:00:00+00:00"},
    {"step_number": 2, "occurred_at": "2026-09-15T13:00:00+00:00"},
    {"step_number": 3, "occurred_at": "2026-09-16T14:00:00+00:00"},
]
SOURCE = (
    '{"header":{"distribution_date":"2026-09-13"},'
    '"steps":[{"occurred_at":"2026-09-14T12:00:00+00:00"},'
    '{"occurred_at":"2026-09-15T13:00:00+00:00"},'
    '{"occurred_at":"2026-09-16T14:00:00+00:00"}]}'
)


def test_accepts_movement_count_equal_to_context_steps() -> None:
    result = validar(
        text="O processo contém 3 movimentações.",
        code=CODE,
        parties=PARTIES,
        steps=STEPS,
    )
    assert result.passed is True


def test_rejects_movement_count_different_from_context_steps() -> None:
    result = validar(
        text="O processo contém 4 movimentos.",
        code=CODE,
        parties=PARTIES,
        steps=STEPS,
    )
    assert result.passed is False
    assert "movement count mismatch: stated 4, expected 3" in result.errors


def test_accepts_date_present_in_source_context_across_rendering_formats() -> None:
    result = validar(
        text="A distribuição ocorreu em 13/09/2026 e houve ato em 2026-09-15.",
        code=CODE,
        parties=PARTIES,
        source_text=SOURCE,
    )
    assert result.passed is True


def test_rejects_date_absent_from_source_context() -> None:
    result = validar(
        text="A audiência ocorreu em 17/09/2026.",
        code=CODE,
        parties=PARTIES,
        source_text=SOURCE,
    )
    assert result.passed is False
    assert "date not present in source context: 2026-09-17" in result.errors


def test_accepts_nonempty_attention_section_when_required() -> None:
    result = validar(
        text="## Pontos de atenção\nNenhuma divergência factual identificada.",
        code=CODE,
        parties=PARTIES,
        require_attention_section=True,
    )
    assert result.passed is True


def test_rejects_missing_attention_section_when_required() -> None:
    result = validar(
        text="## Situação atual\nProcesso em andamento.",
        code=CODE,
        parties=PARTIES,
        require_attention_section=True,
    )
    assert result.passed is False
    assert "Pontos de atenção section is required" in result.errors


def test_rejects_empty_attention_section_when_required() -> None:
    result = validar(
        text="## Pontos de atenção\n\n## Situação atual\nProcesso em andamento.",
        code=CODE,
        parties=PARTIES,
        require_attention_section=True,
    )
    assert result.passed is False
    assert "Pontos de atenção section must not be empty" in result.errors
