from app.prompts import PROCESS_SUMMARY_SYSTEM_PROMPT


def _heading_position(heading: str) -> int:
    format_start = PROCESS_SUMMARY_SYSTEM_PROMPT.index("<formato_de_saida>")
    format_end = PROCESS_SUMMARY_SYSTEM_PROMPT.index("</formato_de_saida>")
    format_contract = PROCESS_SUMMARY_SYSTEM_PROMPT[format_start:format_end]
    position = format_contract.find(heading)
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
    assert "conhecimento externo" in prompt


def test_iasummary_identification_uses_only_structured_available_fields() -> None:
    prompt = PROCESS_SUMMARY_SYSTEM_PROMPT

    for label in (
        "Instância: [header.instance, se disponível]",
        "Área: [header.area, se disponível]",
        "Justiça: [header.justice_description, se disponível]",
        "Comarca: [header.county, se disponível]",
        "Estado: [header.state, se disponível]",
        "Cidade: [header.city, se disponível]",
        "Valor: [header.amount, se disponível]",
    ):
        assert label in prompt

    assert "Não invente órgão julgador, juiz, relator, fase, situação, justiça gratuita" in prompt


def test_iasummary_panorama_is_evidence_bound() -> None:
    prompt = PROCESS_SUMMARY_SYSTEM_PROMPT

    assert "Informe o volume total de movimentos usando step_count" in prompt
    assert "não trate o primeiro movimento como distribuição" in prompt
    assert "nem deduza um próximo evento por expectativa jurídica" in prompt


def test_iasummary_plain_language_explanations_are_glossary_bound() -> None:
    prompt = PROCESS_SUMMARY_SYSTEM_PROMPT

    assert "Quando tpu_glossary estiver presente" in prompt
    assert "usando somente name e definition" in prompt
    assert "Se não houver definição correspondente em tpu_glossary" in prompt
    assert "em vez de usar conhecimento externo" in prompt
    assert "Não cite internamente tpu_version, publisher, source_ref ou definition_sha256" in prompt
