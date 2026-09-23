from app.rag import _provider_payload, _validate_provider_summary


def _context() -> dict:
    return {
        "code": "0000000-00.2026.8.21.0114",
        "court": "TJRS",
        "class_name": "Procedimento Comum",
        "subjects": [{"name": "Obrigação"}],
        "parties": [{"name": "Maria da Silva"}],
        "secrecy_level": 0,
        "header": {"distribution_date": "2026-09-10"},
        "step_count": 200,
        "_process_evidence_ref": "p-00000000000000000000000000000001",
        "_selected_sources": [],
        "_attachment_sources": [],
        "_parsed_summary": {
        "synthesis": "Síntese factual.",
        "timeline": [],
        "current_status": "Situação atual registrada.",
        "attention": ["Nenhuma divergência objetiva identificada."],
        "decisions": [],
        "deadlines": [],
        "related_processes": [],
        "attachments": [],
        "claims": [
            {
                "claim_id": "synthesis",
                "text": "Síntese factual.",
                "evidence_refs": ["p-00000000000000000000000000000001"],
            },
            {
                "claim_id": "current_status",
                "text": "Situação atual registrada.",
                "evidence_refs": ["p-00000000000000000000000000000001"],
            },
            {
                "claim_id": "attention:0",
                "text": "Nenhuma divergência objetiva identificada.",
                "evidence_refs": ["p-00000000000000000000000000000001"],
            },
        ],
    },
        "steps": [
            {
                "step_number": number,
                "occurred_at": "2026-09-15T12:00:00+00:00",
                "title": "Movimento",
                "text": f"Movimento recuperado {number}",
            }
            for number in range(1, 21)
        ],
    }


def test_provider_payload_exposes_total_count_not_only_retrieved_count() -> None:
    process, steps = _provider_payload(_context())
    assert process["step_count"] == 200
    assert len(steps) == 20


def test_wired_validation_uses_total_count_and_exact_provider_dates() -> None:
    result = _validate_provider_summary(
        (
            "# Resumo do processo\n"
            "O processo possui 200 movimentos e foi distribuído em 10/09/2026.\n\n"
            "## Pontos de atenção\nNenhuma divergência objetiva identificada."
        ),
        _context(),
    )
    assert result.passed is True


def test_wired_validation_rejects_retrieved_count_as_total() -> None:
    result = _validate_provider_summary(
        (
            "# Resumo do processo\nO processo possui 20 movimentos.\n\n"
            "## Pontos de atenção\nNenhuma divergência objetiva identificada."
        ),
        _context(),
    )
    assert result.passed is False
    assert "movement count mismatch: stated 20, expected 200" in result.errors


def test_wired_validation_rejects_date_not_sent_to_provider() -> None:
    result = _validate_provider_summary(
        (
            "# Resumo do processo\nAudiência em 16/09/2026.\n\n"
            "## Pontos de atenção\nNenhuma divergência objetiva identificada."
        ),
        _context(),
    )
    assert result.passed is False
    assert "date not present in source context: 2026-09-16" in result.errors


def test_wired_validation_requires_nonempty_attention_section() -> None:
    result = _validate_provider_summary("# Resumo do processo\nProcesso em andamento.", _context())
    assert result.passed is False
    assert "Pontos de atenção section is required" in result.errors
