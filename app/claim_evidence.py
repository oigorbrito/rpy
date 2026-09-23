from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

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


def build_material_claims(
    payload: dict[str, Any],
    *,
    evidence_refs: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    refs = [str(ref) for ref in evidence_refs]
    return [
        {
            "claim_id": claim_id,
            "text": text,
            "evidence_refs": list(refs),
        }
        for claim_id, text in expected_material_claims(payload).items()
    ]


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


def claim_evidence_is_complete(
    structured_output: dict[str, Any] | None,
    claim_evidence: list[dict[str, Any]],
) -> bool:
    if not isinstance(structured_output, dict):
        return False
    summary = structured_output.get("summary")
    if not isinstance(summary, dict):
        return False

    expected = expected_material_claims(summary)
    if not expected:
        return False

    observed: dict[str, dict[str, Any]] = {}
    for item in claim_evidence:
        if not isinstance(item, dict):
            return False
        claim_id = str(item.get("claim_id") or "").strip()
        text = str(item.get("text") or "").strip()
        refs = item.get("evidence_refs")
        if (
            claim_id not in expected
            or claim_id in observed
            or text != expected[claim_id]
            or not isinstance(refs, list)
            or not refs
            or any(not isinstance(ref, str) or not _EVIDENCE_REF_RE.fullmatch(ref) for ref in refs)
        ):
            return False
        observed[claim_id] = item

    return set(observed) == set(expected)


async def replace_summary_claim_evidence(
    conn: asyncpg.Connection,
    *,
    summary_id: UUID,
    process_id: UUID,
    version_id: UUID,
    claims: list[MaterialClaim],
    catalog: dict[str, EvidenceSource],
) -> None:
    await conn.execute(
        "DELETE FROM process_summary_claims WHERE summary_id = $1",
        summary_id,
    )
    if not claims:
        return

    for claim in claims:
        claim_row_id = await conn.fetchval(
            """
            INSERT INTO process_summary_claims (
                summary_id, process_id, version_id, claim_id, claim_class, claim_text
            ) VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            summary_id,
            process_id,
            version_id,
            claim.claim_id,
            claim.claim_class,
            claim.text,
        )
        if claim_row_id is None:
            raise RuntimeError("claim row was not returned")
        records: list[tuple[Any, ...]] = []
        for source_order, ref in enumerate(claim.evidence_refs):
            source = catalog.get(ref)
            if source is None:
                raise ValueError(f"claim evidence ref is not in validated catalog: {ref}")
            records.append(
                (
                    claim_row_id,
                    summary_id,
                    process_id,
                    version_id,
                    ref,
                    source.kind,
                    source.step_id,
                    source.attachment_chunk_id,
                    source_order,
                )
            )
        await conn.executemany(
            """
            INSERT INTO process_summary_claim_sources (
                claim_row_id, summary_id, process_id, version_id,
                evidence_ref, source_kind, step_id, attachment_chunk_id, source_order
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            records,
        )


async def load_summary_claim_evidence(
    conn: asyncpg.Connection,
    *,
    summary_id: UUID,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT c.id, c.claim_id, c.claim_class, c.claim_text,
               COALESCE(
                   jsonb_agg(
                       jsonb_build_object(
                           'evidence_ref', s.evidence_ref,
                           'source_kind', s.source_kind,
                           'source_order', s.source_order
                       )
                       ORDER BY s.source_order
                   ) FILTER (
                       WHERE s.evidence_ref IS NOT NULL
                         AND s.summary_id = c.summary_id
                         AND s.process_id = c.process_id
                         AND s.version_id = c.version_id
                         AND (
                             (
                                 s.source_kind = 'process'
                                 AND s.step_id IS NULL
                                 AND s.attachment_chunk_id IS NULL
                                 AND s.evidence_ref =
                                     'p-' || replace(s.version_id::text, '-', '')
                             )
                             OR (
                                 s.source_kind = 'movement'
                                 AND s.step_id IS NOT NULL
                                 AND s.attachment_chunk_id IS NULL
                                 AND s.evidence_ref =
                                     'm-' || replace(s.step_id::text, '-', '')
                                 AND EXISTS (
                                     SELECT 1
                                     FROM process_summary_sources used
                                     JOIN process_steps step
                                       ON step.id = used.step_id
                                      AND step.process_id = used.process_id
                                      AND step.version_id = used.version_id
                                     WHERE used.summary_id = s.summary_id
                                       AND used.process_id = s.process_id
                                       AND used.version_id = s.version_id
                                       AND used.step_id = s.step_id
                                 )
                             )
                             OR (
                                 s.source_kind = 'attachment'
                                 AND s.step_id IS NULL
                                 AND s.attachment_chunk_id IS NOT NULL
                                 AND s.evidence_ref =
                                     'a-' || replace(
                                         s.attachment_chunk_id::text, '-', ''
                                     )
                                 AND EXISTS (
                                     SELECT 1
                                     FROM process_summary_attachment_sources used
                                     JOIN attachment_chunks chunk
                                       ON chunk.id = used.attachment_chunk_id
                                      AND chunk.process_id = used.process_id
                                      AND chunk.version_id = used.version_id
                                      AND chunk.attachment_id = used.attachment_id
                                     JOIN process_attachments attachment
                                       ON attachment.id = used.attachment_id
                                      AND attachment.process_id = used.process_id
                                      AND attachment.version_id = used.version_id
                                      AND attachment.source_attachment_id =
                                          used.source_attachment_id
                                     WHERE used.summary_id = s.summary_id
                                       AND used.process_id = s.process_id
                                       AND used.version_id = s.version_id
                                       AND used.attachment_chunk_id =
                                           s.attachment_chunk_id
                                 )
                             )
                         )
                   ),
                   '[]'::jsonb
               ) AS sources
        FROM process_summary_claims c
        JOIN process_summaries summary
          ON summary.id = c.summary_id
         AND summary.process_id = c.process_id
         AND summary.version_id = c.version_id
        JOIN process_versions version
          ON version.id = c.version_id
         AND version.process_id = c.process_id
        LEFT JOIN process_summary_claim_sources s ON s.claim_row_id = c.id
        WHERE c.summary_id = $1
        GROUP BY c.id, c.claim_id, c.claim_class, c.claim_text
        ORDER BY c.claim_id
        """,
        summary_id,
    )
    return [
        {
            "claim_id": str(row["claim_id"]),
            "claim_class": str(row["claim_class"]),
            "text": str(row["claim_text"]),
            "evidence_refs": [
                str(item["evidence_ref"])
                for item in list(row["sources"] or [])
            ],
        }
        for row in rows
    ]
