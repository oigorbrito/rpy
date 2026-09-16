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


def test_accepts_required_attention_fact_with_case_and_accent_normalization() -> None:
    fact = "Nenhum movimento processual foi fornecido no payload."
    result = validar(
        text="## Pontos de atenção\nNENHUM MOVIMENTO PROCESSUAL foi fornecido no payload.",
        code=CODE,
        parties=PARTIES,
        require_attention_section=True,
        required_attention_phrases=[fact],
    )
    assert result.passed is True


def test_rejects_missing_required_attention_fact() -> None:
    fact = "Nenhum movimento processual foi fornecido no payload."
    result = validar(
        text="## Pontos de atenção\nNenhuma divergência factual identificada.",
        code=CODE,
        parties=PARTIES,
        require_attention_section=True,
        required_attention_phrases=[fact],
    )
    assert result.passed is False
    assert f"required attention fact missing: {fact}" in result.errors


def test_rejects_required_attention_fact_outside_attention_section() -> None:
    fact = "Nenhum movimento processual foi fornecido no payload."
    result = validar(
        text=(
            "## Síntese\nNenhum movimento processual foi fornecido no payload.\n\n"
            "## Pontos de atenção\nNenhuma divergência factual identificada."
        ),
        code=CODE,
        parties=PARTIES,
        require_attention_section=True,
        required_attention_phrases=[fact],
    )
    assert result.passed is False
    assert f"required attention fact missing: {fact}" in result.errors


def test_rejects_formatted_cpf_even_when_not_present_in_source() -> None:
    result = validar(
        text="Documento informado: 123.456.789-09.",
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is False
    assert "possible unmasked CPF/CNPJ" in result.errors


def test_rejects_formatted_cnpj_even_when_not_present_in_source() -> None:
    result = validar(
        text="Documento informado: 12.345.678/0001-90.",
        code=CODE,
        parties=PARTIES,
    )
    assert result.passed is False
    assert "possible unmasked CPF/CNPJ" in result.errors


def test_rejects_party_identifier_rendered_with_different_formatting() -> None:
    parties = [
        {
            "name": "Maria da Silva",
            "documents": [{"document_number": "1234567890"}],
        }
    ]
    result = validar(
        text="Identificador da parte: 123-456-789-0.",
        code=CODE,
        parties=parties,
    )
    assert result.passed is False
    assert "personal identifier from party data is prohibited" in result.errors


def test_accepts_unrelated_number_when_party_identifier_differs() -> None:
    parties = [{"name": "Maria da Silva", "person_id": "1234567890"}]
    result = validar(
        text="Referência interna sintética: 987-654-321-0.",
        code=CODE,
        parties=parties,
    )
    assert "personal identifier from party data is prohibited" not in result.errors


def test_secret_mode_rejects_source_party_name_without_role_label() -> None:
    result = validar(
        text="O conteúdo menciona Maria da Silva em texto corrido.",
        code=CODE,
        parties=PARTIES,
        forbid_party_names=True,
    )
    assert result.passed is False
    assert "party names are prohibited for secret summary" in result.errors


def test_secret_mode_accepts_text_without_source_party_name() -> None:
    result = validar(
        text="Os detalhes processuais foram restringidos por sigilo.",
        code=CODE,
        parties=PARTIES,
        forbid_party_names=True,
    )
    assert result.passed is True
