from __future__ import annotations

from uuid import UUID

from app.claim_evidence import (
    attachment_evidence_ref,
    build_material_claims,
    evidence_catalog,
    movement_evidence_ref,
    process_evidence_ref,
    validate_claim_evidence,
)


VERSION_ID = UUID("00000000-0000-0000-0000-000000000101")
STEP_ID = UUID("00000000-0000-0000-0000-000000000202")
CHUNK_ID = UUID("00000000-0000-0000-0000-000000000303")


def _context() -> dict:
    return {
        "_process_evidence_ref": process_evidence_ref(VERSION_ID),
        "_selected_sources": [
            {
                "step_id": STEP_ID,
                "evidence_ref": movement_evidence_ref(STEP_ID),
            }
        ],
        "_attachment_sources": [
            {
                "attachment_chunk_id": CHUNK_ID,
                "evidence_ref": attachment_evidence_ref(CHUNK_ID),
            }
        ],
    }


def _payload() -> dict:
    return {
        "synthesis": "Síntese factual.",
        "timeline": ["Movimento relevante."],
        "current_status": "Situação atual.",
        "attention": ["Sem divergência material."],
        "decisions": ["Pedido deferido."],
        "deadlines": [],
        "related_processes": [],
        "attachments": ["Documento relevante."],
    }


def test_evidence_catalog_accepts_only_scoped_application_refs() -> None:
    catalog = evidence_catalog(_context())

    assert set(catalog) == {
        process_evidence_ref(VERSION_ID),
        movement_evidence_ref(STEP_ID),
        attachment_evidence_ref(CHUNK_ID),
    }
    assert catalog[process_evidence_ref(VERSION_ID)].kind == "process"
    assert catalog[movement_evidence_ref(STEP_ID)].step_id == STEP_ID
    assert catalog[attachment_evidence_ref(CHUNK_ID)].attachment_chunk_id == CHUNK_ID


def test_valid_claims_cover_process_movement_and_attachment_refs() -> None:
    payload = _payload()
    payload["claims"] = [
        {
            "claim_id": "synthesis",
            "text": payload["synthesis"],
            "evidence_refs": [process_evidence_ref(VERSION_ID)],
        },
        {
            "claim_id": "current_status",
            "text": payload["current_status"],
            "evidence_refs": [movement_evidence_ref(STEP_ID)],
        },
        {
            "claim_id": "timeline:0",
            "text": payload["timeline"][0],
            "evidence_refs": [movement_evidence_ref(STEP_ID)],
        },
        {
            "claim_id": "decisions:0",
            "text": payload["decisions"][0],
            "evidence_refs": [movement_evidence_ref(STEP_ID)],
        },
        {
            "claim_id": "attachments:0",
            "text": payload["attachments"][0],
            "evidence_refs": [attachment_evidence_ref(CHUNK_ID)],
        },
    ]

    claims, errors = validate_claim_evidence(payload, _context())

    assert errors == []
    assert [claim.claim_id for claim in claims] == [
        "synthesis",
        "current_status",
        "timeline:0",
        "decisions:0",
        "attachments:0",
    ]


def test_missing_unknown_duplicate_and_cross_scope_refs_fail_closed() -> None:
    payload = _payload()
    other_step = UUID("00000000-0000-0000-0000-000000000999")
    payload["claims"] = [
        {
            "claim_id": "synthesis",
            "text": payload["synthesis"],
            "evidence_refs": [],
        },
        {
            "claim_id": "current_status",
            "text": payload["current_status"],
            "evidence_refs": [movement_evidence_ref(other_step)],
        },
        {
            "claim_id": "timeline:0",
            "text": payload["timeline"][0],
            "evidence_refs": [
                movement_evidence_ref(STEP_ID),
                movement_evidence_ref(STEP_ID),
            ],
        },
        {
            "claim_id": "decisions:0",
            "text": "Texto divergente.",
            "evidence_refs": [movement_evidence_ref(STEP_ID)],
        },
    ]

    _, errors = validate_claim_evidence(payload, _context())

    assert "material claim lacks evidence_refs: synthesis" in errors
    assert any(error.startswith("unknown evidence ref for current_status:") for error in errors)
    assert any(error.startswith("duplicate evidence ref for timeline:0:") for error in errors)
    assert "claim text does not match structured field: decisions:0" in errors
    assert "missing material claim provenance: attachments:0" in errors


def test_missing_claims_array_is_rejected() -> None:
    claims, errors = validate_claim_evidence(_payload(), _context())

    assert claims == []
    assert errors == ["structured summary must include claims"]


def test_deterministic_builder_covers_every_material_field() -> None:
    payload = _payload()
    refs = [movement_evidence_ref(STEP_ID)]

    built = build_material_claims(payload, evidence_refs=refs)

    assert [item["claim_id"] for item in built] == [
        "synthesis",
        "current_status",
        "timeline:0",
        "decisions:0",
        "attachments:0",
    ]
    assert all(item["evidence_refs"] == refs for item in built)
