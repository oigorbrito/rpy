from app.prompts import PROCESS_SUMMARY_SYSTEM_PROMPT


def _heading_position(heading: str) -> int:
    position = PROCESS_SUMMARY_SYSTEM_PROMPT.find(heading)
    assert position >= 0, f"missing summary contract heading: {heading}"
    return position


def test_iasummary_document_sections_have_stable_order() -> None:
    headings = [
        "# Resumo do processo",
        '<ProcessHeader className="process-header">',
        "## Partes",
        "## Síntese",
        "## Linha do tempo relevante",
        "## Situação atual",
        "## Pontos de atenção",
    ]

    positions = [_heading_position(heading) for heading in headings]
    assert positions == sorted(positions)


def test_iasummary_conditional_sections_are_evidence_bound() -> None:
    prompt = PROCESS_SUMMARY_SYSTEM_PROMPT

    assert "1. Decisões" in prompt
    assert "2. Prazos em curso" in prompt
    assert "3. Processos relacionados" in prompt
    assert "4. Anexos" in prompt
    assert "Essas seções são condicionais" in prompt
    assert "omita-a" in prompt


def test_iasummary_contract_is_not_defined_by_frontend_or_external_summary() -> None:
    prompt = PROCESS_SUMMARY_SYSTEM_PROMPT

    assert "Não copie nem resuma qualquer registro externo `summary`" in prompt
    assert "processo e movimentos autorizados" in prompt
    assert "Não explique mecanismos internos" in prompt
    assert "não use conhecimento externo" in prompt
