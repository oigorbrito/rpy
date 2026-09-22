from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

_EVIDENCE_REF_RE = re.compile(r"^[pma]-[0-9a-f]{32}$")
_CLAIM_ID_RE = re.compile(
    r"^(?:synthesis|current_status|"
    r"(?:timeline|decisions|deadlines|related_processes|attachments):[0-9]+)$"
)
_MATERIAL_LIST_FIELDS = (
    "timeline",
    "decisions",
    "deadlines",
    "related_processes",
    "attachments",
)


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    evidence_ref: str
    kind: str
    step_id: UUID | None = None
    attachment_chunk_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MaterialClaim:
    claim_id: str
    claim_class: str
    text: str
    evidence_refs: tuple[str, ...]


def process_evidence_ref(version_id: UUID) -> str:
    return f"p-{version_id.hex}"


def movement_evidence_ref(step_id: UUID) -> str:
    return f"m-{step_id.hex}"


def attachment_evidence_ref(chunk_id: UUID) -> str:
    return f"a-{chunk_id.hex}"


def _claim_class(claim_id: str) -> str:
    prefix = claim_id.split(":", 1)[0]
    return {
        "synthesis": "synthesis",
        "current_status": "current_status",
        "timeline": "procedural_event",
        "decisions": "decision",
        "deadlines": "deadline",
        "related_processes": "related_process",
        "attachments": "attachment",
    }[prefix]


def expected_material_claims(payload: dict[str, Any]) -> dict[str, str]:
    expected = {
        "synthesis": str(payload.get("synthesis") or ""),
        "current_status": str(payload.get("current_status") or ""),
    }
    for field in _MATERIAL_LIST_FIELDS:
        items = payload.get(field)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            expected[f"{field}:{index}"] = str(item)
    return expected


def evidence_catalog(context: dict[str, Any]) -> dict[str, EvidenceSource]:
    catalog: dict[str, EvidenceSource] = {}
    process_ref = str(context.get("_process_evidence_ref") or "")
    if _EVIDENCE_REF_RE.fullmatch(process_ref):
        catalog[process_ref] = EvidenceSource(process_ref, "process")

    for source in context.get("_selected_sources", []):
        if not isinstance(source, dict):
            continue
        ref = str(source.get("evidence_ref") or "")
        step_id = source.get("step_id")
        if _EVIDENCE_REF_RE.fullmatch(ref) and isinstance(step_id, UUID):
            catalog[ref] = EvidenceSource(ref, "movement", step_id=step_id)

    for source in context.get("_attachment_sources", []):
        if not isinstance(source, dict):
            continue
        ref = str(source.get("evidence_ref") or "")
        chunk_id = source.get("attachment_chunk_id")
        if _EVIDENCE_REF_RE.fullmatch(ref) and isinstance(chunk_id, UUID):
            catalog[ref] = EvidenceSource(
                ref,
                "attachment",
                attachment_chunk_id=chunk_id,
            )
    return catalog


def validate_claim_evidence(
    payload: dict[str, Any],
    context: dict[str, Any],
) -> tuple[list[MaterialClaim], list[str]]:
    expected = expected_material_claims(payload)
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list):
        return [], ["structured summary must include claims"]

    catalog = evidence_catalog(context)
    claims: list[MaterialClaim] = []
    errors: list[str] = []
    seen: set[str] = set()

    for raw in raw_claims:
        if not isinstance(raw, dict):
            errors.append("structured summary claim must be an object")
            continue
        claim_id = str(raw.get("claim_id") or "").strip()
        text = str(raw.get("text") or "").strip()
        refs = raw.get("evidence_refs")
        if not _CLAIM_ID_RE.fullmatch(claim_id):
            errors.append(f"invalid claim_id: {claim_id or '<empty>'}")
            continue
        if claim_id in seen:
            errors.append(f"duplicate claim_id: {claim_id}")
            continue
        seen.add(claim_id)
        if claim_id not in expected:
            errors.append(f"unexpected material claim: {claim_id}")
            continue
        if text != expected[claim_id]:
            errors.append(f"claim text does not match structured field: {claim_id}")
        if not isinstance(refs, list) or not refs:
            errors.append(f"material claim lacks evidence_refs: {claim_id}")
            refs = []
        rendered_refs: list[str] = []
        seen_refs: set[str] = set()
        for ref_value in refs:
            ref = str(ref_value or "").strip()
            if ref in seen_refs:
                errors.append(f"duplicate evidence ref for {claim_id}: {ref}")
                continue
            seen_refs.add(ref)
            if not _EVIDENCE_REF_RE.fullmatch(ref):
                errors.append(f"invalid evidence ref for {claim_id}: {ref or '<empty>'}")
                continue
            if ref not in catalog:
                errors.append(f"unknown evidence ref for {claim_id}: {ref}")
                continue
            rendered_refs.append(ref)
        claims.append(
            MaterialClaim(
                claim_id=claim_id,
                claim_class=_claim_class(claim_id),
                text=text,
                evidence_refs=tuple(rendered_refs),
            )
        )

    for claim_id in expected:
        if claim_id not in seen:
            errors.append(f"missing material claim provenance: {claim_id}")

    return claims, errors
