from app.prompts import PROCESS_SUMMARY_SYSTEM_PROMPT


def _heading_position(heading: str) -> int:
    position = PROCESS_SUMMARY_SYSTEM_PROMPT.find(heading)
    assert position >= 0, f"missing summary contract heading: {heading}"
    return position


def test_iasummary_document_sections_have_stable_order() -> None:
    headings = [
        "# Resumo do processo",
        "## Partes",
        "## Classe",
        "## Assuntos",
        "## Movimentações",
        "## Pontos de atenção",
    ]

    positions = [_heading_position(heading) for heading in headings]
    assert positions == sorted(positions)


def test_iasummary_conditional_sections_are_evidence_bound() -> None:
    prompt = PROCESS_SUMMARY_SYSTEM_PROMPT

    assert '"Decisões", "Prazos em curso", "Processos relacionados" e "Anexos"' in prompt
    assert "Inclua somente quando houver evidência" in prompt
    assert "Se nenhuma existir" in prompt
    assert "não está suficientemente determinado no contexto fornecido" in prompt


def test_iasummary_contract_is_not_defined_by_frontend_or_external_summary() -> None:
    prompt = PROCESS_SUMMARY_SYSTEM_PROMPT

    assert "Não copie nem" in prompt
    assert "registro externo `summary`" in prompt
    assert "Não explique mecanismos internos" in prompt
    assert "não use conhecimento externo" in prompt
