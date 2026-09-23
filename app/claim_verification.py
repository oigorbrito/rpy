from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, Sequence
from zoneinfo import ZoneInfo

_EVIDENCE_REF_RE = re.compile(r"^[pma]-[0-9a-f]{32}$")
_CNJ_RE = re.compile(r"\b\d{7}-?\d{2}\.?\d{4}\.?\d\.?\d{2}\.?\d{4}\b")
_DATE_RE = re.compile(
    r"(?<!\d)(?:\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])|"
    r"(?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])/\d{4})(?!\d)"
)
_ISO_DATETIME_RE = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})(?!\d)"
)
_AMOUNT_RE = re.compile(
    r"(?<!\w)(?:R\$|BRL)\s*-?\d(?:[\d.\s]*\d)?(?:,\d+)?(?!\w)",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")
_SAO_PAULO = ZoneInfo("America/Sao_Paulo")
_MAX_EXCERPT_CHARS = 4000
VERIFICATION_STATUSES = frozenset(
    {"supported", "contradicted", "insufficient", "not_evaluated"}
)


class ClaimLike(Protocol):
    claim_id: str
    claim_class: str
    text: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    evidence_ref: str
    kind: str
    text: str
    cnjs: frozenset[str]
    dates: frozenset[str]
    amounts: frozenset[Decimal]
    party_names: frozenset[str]
    page_start: int | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None


@dataclass(frozen=True, slots=True)
class ClaimRelationVerification:
    evidence_ref: str
    status: str
    reason: str
    evidence_excerpt: str | None
    evidence_excerpt_sha256: str | None
    page_start: int | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None


@dataclass(frozen=True, slots=True)
class ClaimVerification:
    claim_id: str
    status: str
    reason: str
    deterministic_fact_count: int
    relations: tuple[ClaimRelationVerification, ...]


def _normalized_text(value: Any) -> str:
    rendered = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _SPACE_RE.sub(" ", rendered).strip()


def _canonical_cnj(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _cnjs(text: str) -> frozenset[str]:
    return frozenset(_canonical_cnj(match.group(0)) for match in _CNJ_RE.finditer(text))


def _canonical_date(value: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _dates(text: str) -> frozenset[str]:
    values: set[str] = set()
    for match in _DATE_RE.finditer(text):
        canonical = _canonical_date(match.group(0))
        if canonical is not None:
            values.add(canonical)
    for match in _ISO_DATETIME_RE.finditer(text):
        try:
            value = datetime.fromisoformat(match.group(0).replace("Z", "+00:00"))
        except ValueError:
            continue
        if value.tzinfo is not None:
            values.add(value.astimezone(_SAO_PAULO).date().isoformat())
    return frozenset(values)


def _decimal_amount(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    rendered = str(value).strip().replace("\u00a0", " ")
    rendered = re.sub(r"^(?:R\$|BRL)\s*", "", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"\s+", "", rendered)
    if not rendered:
        return None
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?", rendered):
        rendered = rendered.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d+,\d+", rendered):
        rendered = rendered.replace(",", ".")
    elif not re.fullmatch(r"-?\d+(?:\.\d+)?", rendered):
        return None
    try:
        return Decimal(rendered)
    except InvalidOperation:
        return None


def _amounts(text: str) -> frozenset[Decimal]:
    values: set[Decimal] = set()
    for match in _AMOUNT_RE.finditer(text):
        parsed = _decimal_amount(match.group(0))
        if parsed is not None:
            values.add(parsed)
    return frozenset(values)


def _known_party_names(context: dict[str, Any]) -> frozenset[str]:
    values: set[str] = set()
    for party in context.get("parties", []):
        if not isinstance(party, dict):
            continue
        name = _normalized_text(party.get("name"))
        if name:
            values.add(name)
    return frozenset(values)


def _party_names_in_text(text: str, known: frozenset[str]) -> frozenset[str]:
    normalized = f" {_normalized_text(text)} "
    return frozenset(name for name in known if f" {name} " in normalized)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _verification_evidence(
    *,
    evidence_ref: str,
    kind: str,
    text: str,
    known_parties: frozenset[str],
    amounts: frozenset[Decimal] | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    char_start: int | None = None,
    char_end: int | None = None,
) -> VerificationEvidence:
    return VerificationEvidence(
        evidence_ref=evidence_ref,
        kind=kind,
        text=text,
        cnjs=_cnjs(text),
        dates=_dates(text),
        amounts=frozenset(amounts if amounts is not None else _amounts(text)),
        party_names=_party_names_in_text(text, known_parties),
        page_start=page_start,
        page_end=page_end,
        char_start=char_start,
        char_end=char_end,
    )


def verification_evidence_catalog(
    context: dict[str, Any],
) -> dict[str, VerificationEvidence]:
    known_parties = _known_party_names(context)
    catalog: dict[str, VerificationEvidence] = {}

    process_ref = str(context.get("_process_evidence_ref") or "")
    if _EVIDENCE_REF_RE.fullmatch(process_ref):
        process_payload = {
            "code": context.get("code"),
            "court": context.get("court"),
            "class_name": context.get("class_name"),
            "header": context.get("header") if isinstance(context.get("header"), dict) else {},
            "parties": context.get("parties") if isinstance(context.get("parties"), list) else [],
        }
        process_amounts: set[Decimal] = set()
        header = process_payload["header"]
        if isinstance(header, dict):
            parsed_amount = _decimal_amount(header.get("amount"))
            if parsed_amount is not None:
                process_amounts.add(parsed_amount)
        catalog[process_ref] = _verification_evidence(
            evidence_ref=process_ref,
            kind="process",
            text=_canonical_json(process_payload),
            known_parties=known_parties,
            amounts=frozenset(process_amounts),
        )

    for step in context.get("steps", []):
        if not isinstance(step, dict):
            continue
        ref = str(step.get("evidence_ref") or "")
        if not _EVIDENCE_REF_RE.fullmatch(ref):
            continue
        payload = {
            "step_number": step.get("step_number"),
            "occurred_at": step.get("occurred_at"),
            "title": step.get("title"),
            "text": step.get("text"),
        }
        catalog[ref] = _verification_evidence(
            evidence_ref=ref,
            kind="movement",
            text=_canonical_json(payload),
            known_parties=known_parties,
        )

    for attachment in context.get("attachments", []):
        if not isinstance(attachment, dict):
            continue
        ref = str(attachment.get("evidence_ref") or "")
        if not _EVIDENCE_REF_RE.fullmatch(ref):
            continue
        catalog[ref] = _verification_evidence(
            evidence_ref=ref,
            kind="attachment",
            text=str(attachment.get("text") or ""),
            known_parties=known_parties,
            page_start=_optional_int(attachment.get("page_start")),
            page_end=_optional_int(attachment.get("page_end")),
            char_start=_optional_int(attachment.get("char_start")),
            char_end=_optional_int(attachment.get("char_end")),
        )
    return catalog


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _relation_status(
    *,
    claim_text: str,
    evidence: VerificationEvidence,
    known_parties: frozenset[str],
) -> tuple[str, str, int]:
    normalized_claim = _normalized_text(claim_text)
    normalized_evidence = _normalized_text(evidence.text)
    if normalized_claim and normalized_claim in normalized_evidence:
        return "supported", "exact_text_present", 1

    claim_cnjs = _cnjs(claim_text)
    claim_dates = _dates(claim_text)
    claim_amounts = _amounts(claim_text)
    claim_parties = _party_names_in_text(claim_text, known_parties)
    fact_count = (
        len(claim_cnjs)
        + len(claim_dates)
        + len(claim_amounts)
        + len(claim_parties)
    )
    if fact_count == 0:
        return "not_evaluated", "no_deterministic_fact_anchor", 0

    if claim_cnjs and not claim_cnjs.issubset(evidence.cnjs):
        if evidence.kind == "process" and evidence.cnjs:
            return "contradicted", "process_cnj_mismatch", fact_count
        return "insufficient", "cited_source_missing_cnj", fact_count

    if claim_amounts and not claim_amounts.issubset(evidence.amounts):
        if evidence.kind == "process" and evidence.amounts:
            return "contradicted", "process_amount_mismatch", fact_count
        return "insufficient", "cited_source_missing_amount", fact_count

    if claim_dates and not claim_dates.issubset(evidence.dates):
        return "insufficient", "cited_source_missing_date", fact_count

    if claim_parties and not claim_parties.issubset(evidence.party_names):
        return "insufficient", "cited_source_missing_party", fact_count

    return "supported", "deterministic_facts_present", fact_count


def _excerpt(value: str) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    rendered = value[:_MAX_EXCERPT_CHARS]
    return rendered, hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def verify_material_claims(
    claims: Sequence[ClaimLike],
    context: dict[str, Any],
) -> dict[str, ClaimVerification]:
    catalog = verification_evidence_catalog(context)
    known_parties = _known_party_names(context)
    results: dict[str, ClaimVerification] = {}

    for claim in claims:
        relations: list[ClaimRelationVerification] = []
        total_fact_count = 0
        for ref in claim.evidence_refs:
            evidence = catalog.get(ref)
            if evidence is None:
                relations.append(
                    ClaimRelationVerification(
                        evidence_ref=ref,
                        status="insufficient",
                        reason="verification_source_unavailable",
                        evidence_excerpt=None,
                        evidence_excerpt_sha256=None,
                    )
                )
                continue
            status, reason, fact_count = _relation_status(
                claim_text=claim.text,
                evidence=evidence,
                known_parties=known_parties,
            )
            total_fact_count = max(total_fact_count, fact_count)
            excerpt, excerpt_sha256 = _excerpt(evidence.text)
            relations.append(
                ClaimRelationVerification(
                    evidence_ref=ref,
                    status=status,
                    reason=reason,
                    evidence_excerpt=excerpt,
                    evidence_excerpt_sha256=excerpt_sha256,
                    page_start=evidence.page_start,
                    page_end=evidence.page_end,
                    char_start=evidence.char_start,
                    char_end=evidence.char_end,
                )
            )

        statuses = {relation.status for relation in relations}
        if "contradicted" in statuses:
            status = "contradicted"
            reason = "at_least_one_cited_source_contradicts_deterministic_fact"
        elif "supported" in statuses:
            status = "supported"
            reason = "at_least_one_cited_source_supports_deterministic_fact"
        elif "insufficient" in statuses:
            status = "insufficient"
            reason = "cited_sources_do_not_support_deterministic_fact"
        else:
            status = "not_evaluated"
            reason = "no_deterministic_fact_anchor"

        results[claim.claim_id] = ClaimVerification(
            claim_id=claim.claim_id,
            status=status,
            reason=reason,
            deterministic_fact_count=total_fact_count,
            relations=tuple(relations),
        )
    return results


def verification_errors(
    verification: dict[str, ClaimVerification],
) -> list[str]:
    errors: list[str] = []
    for claim_id, result in verification.items():
        if result.status == "contradicted":
            errors.append(f"claim contradicted by cited evidence: {claim_id}")
        elif result.status == "insufficient" and result.deterministic_fact_count > 0:
            errors.append(f"claim has insufficient cited evidence: {claim_id}")
    return errors
