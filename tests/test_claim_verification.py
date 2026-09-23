from __future__ import annotations

from uuid import UUID

from app.claim_evidence import MaterialClaim, process_evidence_ref
from app.claim_verification import verification_errors, verify_material_claims


VERSION_ID = UUID("00000000-0000-0000-0000-000000000101")
STEP_ID = UUID("00000000-0000-0000-0000-000000000102")
CHUNK_ID = UUID("00000000-0000-0000-0000-000000000103")


def _context() -> dict:
    return {
        "code": "0000000-00.2026.8.21.0001",
        "court": "TJRS",
        "class_name": "Procedimento Comum",
        "header": {"amount": "1000.00"},
        "parties": [{"name": "Maria da Silva", "side": "active"}],
        "_process_evidence_ref": process_evidence_ref(VERSION_ID),
        "_selected_sources": [
            {
                "evidence_ref": f"m-{STEP_ID.hex}",
                "step_id": STEP_ID,
            }
        ],
        "_attachment_sources": [
            {
                "evidence_ref": f"a-{CHUNK_ID.hex}",
                "attachment_chunk_id": CHUNK_ID,
            }
        ],
        "steps": [
            {
                "evidence_ref": f"m-{STEP_ID.hex}",
                "step_number": 1,
                "occurred_at": "2026-09-10T12:00:00-03:00",
                "title": "Audiência",
                "text": "Audiência realizada em 10/09/2026.",
            }
        ],
        "attachments": [
            {
                "evidence_ref": f"a-{CHUNK_ID.hex}",
                "source_attachment_id": "doc-1",
                "page_start": 2,
                "page_end": 2,
                "char_start": 120,
                "char_end": 180,
                "text": "Decisão anexada determina pagamento de R$ 1.000,00.",
            }
        ],
    }


def test_process_amount_mismatch_is_contradicted() -> None:
    claim = MaterialClaim(
        claim_id="synthesis",
        claim_class="synthesis",
        text="Valor da causa: R$ 2.000,00.",
        evidence_refs=(process_evidence_ref(VERSION_ID),),
    )

    verification = verify_material_claims([claim], _context())

    result = verification["synthesis"]
    assert result.status == "contradicted"
    assert result.relations[0].status == "contradicted"
    assert verification_errors(verification) == [
        "claim contradicted by cited evidence: synthesis"
    ]


def test_movement_date_anchor_is_supported() -> None:
    claim = MaterialClaim(
        claim_id="timeline:0",
        claim_class="procedural_event",
        text="Audiência realizada em 10/09/2026.",
        evidence_refs=(f"m-{STEP_ID.hex}",),
    )

    result = verify_material_claims([claim], _context())["timeline:0"]

    assert result.status == "supported"
    assert result.deterministic_fact_count >= 1
    assert result.relations[0].status == "supported"


def test_missing_deterministic_date_is_insufficient_and_retryable() -> None:
    claim = MaterialClaim(
        claim_id="timeline:0",
        claim_class="procedural_event",
        text="Audiência realizada em 11/09/2026.",
        evidence_refs=(f"m-{STEP_ID.hex}",),
    )

    verification = verify_material_claims([claim], _context())

    assert verification["timeline:0"].status == "insufficient"
    assert verification_errors(verification) == [
        "claim has insufficient cited evidence: timeline:0"
    ]


def test_unanchored_semantic_claim_remains_explicitly_not_evaluated() -> None:
    claim = MaterialClaim(
        claim_id="current_status",
        claim_class="current_status",
        text="O processo segue em andamento.",
        evidence_refs=(f"m-{STEP_ID.hex}",),
    )

    verification = verify_material_claims([claim], _context())

    assert verification["current_status"].status == "not_evaluated"
    assert verification["current_status"].deterministic_fact_count == 0
    assert verification_errors(verification) == []


def test_attachment_verification_preserves_available_positions_and_excerpt_hash() -> None:
    claim = MaterialClaim(
        claim_id="attachments:0",
        claim_class="attachment",
        text="Decisão anexada determina pagamento de R$ 1.000,00.",
        evidence_refs=(f"a-{CHUNK_ID.hex}",),
    )

    result = verify_material_claims([claim], _context())["attachments:0"]
    relation = result.relations[0]

    assert result.status == "supported"
    assert relation.status == "supported"
    assert relation.page_start == 2
    assert relation.page_end == 2
    assert relation.char_start == 120
    assert relation.char_end == 180
    assert relation.evidence_excerpt is not None
    assert relation.evidence_excerpt_sha256 is not None
    assert len(relation.evidence_excerpt_sha256) == 64


def test_any_cited_canonical_contradiction_wins_over_other_support() -> None:
    context = _context()
    context["attachments"][0]["text"] = "Valor da causa: R$ 2.000,00."
    claim = MaterialClaim(
        claim_id="synthesis",
        claim_class="synthesis",
        text="Valor da causa: R$ 2.000,00.",
        evidence_refs=(
            process_evidence_ref(VERSION_ID),
            f"a-{CHUNK_ID.hex}",
        ),
    )

    verification = verify_material_claims([claim], context)
    result = verification["synthesis"]

    assert result.status == "contradicted"
    assert {relation.status for relation in result.relations} == {
        "contradicted",
        "supported",
    }
    assert verification_errors(verification) == [
        "claim contradicted by cited evidence: synthesis"
    ]


def test_multiple_cited_sources_can_support_different_deterministic_facts() -> None:
    claim = MaterialClaim(
        claim_id="decisions:0",
        claim_class="decision",
        text="Em 10/09/2026 foi registrado valor de R$ 1.000,00.",
        evidence_refs=(
            f"m-{STEP_ID.hex}",
            process_evidence_ref(VERSION_ID),
        ),
    )

    result = verify_material_claims([claim], _context())["decisions:0"]

    # Each source alone is incomplete for the full sentence. The first deterministic
    # baseline therefore does not overclaim semantic entailment across sources.
    assert result.status == "supported"
    assert {relation.status for relation in result.relations} == {"insufficient"}
