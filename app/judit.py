from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

_CNJ_CANONICAL_RE = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")
_CNJ_DIGITS_RE = re.compile(r"^\d{20}$")
_PERSONAL_ID_RE = re.compile(r"(?<!\d)(?:\d{11}|\d{14})(?!\d)")
_LEADING_STEP_NUMBER_RE = re.compile(r"^\s*\d+\s*(?:[-–—.:)]\s*|\s+)")
_ELETRONICA_REFER_RE = re.compile(r"\bELETRÔNICAREFER\b", re.IGNORECASE)
_GLUE_BOUNDARY_RE = re.compile(r"(?<=[a-záàâãéêíóôõúç])(?=[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ])")
_WHITESPACE_RE = re.compile(r"\s+")
_SAO_PAULO = ZoneInfo("America/Sao_Paulo")
_SOURCE_STEP_NUMBER_KEYS = (
    "source_step_number",
    "step_number",
    "event_number",
    "movement_number",
    "sequence_number",
)
_SECRET_HEADER_KEYS = (
    "instance",
    "area",
    "justice_description",
    "county",
    "state",
    "city",
)
_PUBLIC_HEADER_KEYS = (
    "name",
    *_SECRET_HEADER_KEYS,
    "amount",
)


@dataclass(frozen=True, slots=True)
class JuditAttachmentRef:
    attachment_id: str
    instance: int
    status: str
    extension: str | None = None
    step_id: str | None = None


@dataclass(slots=True)
class JuditEvent:
    event_type: str
    request_id: str | None
    callback_id: str | None
    response_id: str | None
    response_type: str | None
    code: str | None
    cached_response: bool
    raw: dict[str, Any]
    response_data: dict[str, Any] | None

    @property
    def request_completed(self) -> bool:
        if self.event_type == "request_completed":
            return True
        if self.event_type != "response_created" or self.response_type != "application_info":
            return False
        data = self.response_data or {}
        message = str(data.get("message") or "").strip().upper()
        try:
            info_code = int(data.get("code")) if data.get("code") is not None else None
        except (TypeError, ValueError):
            info_code = None
        return message == "REQUEST_COMPLETED" or info_code == 600

    @property
    def is_lawsuit_response(self) -> bool:
        return self.event_type == "response_created" and self.response_type == "lawsuit"


def normalize_cnj(value: str) -> str:
    candidate = value.strip()
    if not (
        _CNJ_CANONICAL_RE.fullmatch(candidate)
        or _CNJ_DIGITS_RE.fullmatch(candidate)
    ):
        raise ValueError("invalid CNJ process code")
    digits = "".join(character for character in candidate if character.isdigit())
    return (
        f"{digits[:7]}-{digits[7:9]}."
        f"{digits[9:13]}.{digits[13]}.{digits[14:16]}.{digits[16:20]}"
    )


def parse_event(body: dict[str, Any]) -> JuditEvent:
    if not isinstance(body, dict):
        raise ValueError("invalid webhook envelope")

    event_type = str(body.get("event_type") or "").strip().lower()
    reference_type = str(body.get("reference_type") or "").strip().lower()
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}

    explicit_request_id = (
        payload.get("request_id")
        or body.get("request_id")
        or body.get("requestId")
    )
    request_id = (
        explicit_request_id
        if explicit_request_id
        else (body.get("reference_id") if reference_type != "tracking" else None)
    )
    callback_id = body.get("callback_id")
    response_id = payload.get("response_id")
    response_type = payload.get("response_type")
    response_data = payload.get("response_data")
    if response_data is not None and not isinstance(response_data, dict):
        response_data = None

    if not event_type:
        status = str(body.get("status") or "").lower()
        event_type = (
            "request_completed"
            if status in {"completed", "request_completed"}
            else "response_created"
        )
        if response_data is None:
            response_data = body
        response_type = response_type or "lawsuit"

    tags = payload.get("tags") if isinstance(payload.get("tags"), dict) else {}
    cached_response = bool(
        tags.get("cached_response")
        if "cached_response" in tags
        else payload.get("cached_response", body.get("cached_response", False))
    )

    code: str | None = None
    if isinstance(response_data, dict):
        value = response_data.get("code") or response_data.get("process_code")
        if value:
            code = str(value).strip() or None

    event = JuditEvent(
        event_type=event_type,
        request_id=str(request_id) if request_id else None,
        callback_id=str(callback_id) if callback_id else None,
        response_id=str(response_id) if response_id else None,
        response_type=str(response_type) if response_type else None,
        code=code,
        cached_response=cached_response,
        raw=body,
        response_data=response_data,
    )

    if event.is_lawsuit_response:
        if not event.code:
            raise ValueError("lawsuit response missing process code")
        event.code = normalize_cnj(event.code)
        if not event.request_id:
            raise ValueError("lawsuit response missing request id")
        if not (event.response_id or event.callback_id):
            raise ValueError("lawsuit response missing stable response identifier")

    if event.request_completed and not event.request_id:
        raise ValueError("request completion missing request id")

    return event


def _personal_id_digits(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits if len(digits) in {11, 14} else None


def _party_personal_id(party: dict[str, Any]) -> str | None:
    direct = _personal_id_digits(party.get("main_document"))
    if direct:
        return direct
    for document in party.get("documents") or []:
        if not isinstance(document, dict):
            continue
        candidate = _personal_id_digits(document.get("document"))
        if candidate:
            return candidate
    return None


def _masked_personal_id(value: str) -> str:
    if len(value) == 11:
        return f"***.***.***-{value[-2:]}"
    if len(value) == 14:
        return f"**.***.***/****-{value[-2:]}"
    raise ValueError("personal id must contain 11 or 14 digits")


def _safe_parties(process: dict[str, Any]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for party in process.get("parties") or []:
        if not isinstance(party, dict):
            continue
        name = str(party.get("name") or "").strip()
        if not name:
            continue
        normalized = {
            "name": name,
            "side": party.get("side"),
            "person_type": party.get("person_type"),
        }
        personal_id = _party_personal_id(party)
        if personal_id:
            normalized["masked_person_id"] = _masked_personal_id(personal_id)
        safe.append(normalized)
    return safe


def _safe_subjects(process: dict[str, Any]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for subject in process.get("subjects") or []:
        if isinstance(subject, dict):
            safe.append({"code": subject.get("code"), "name": subject.get("name")})
        elif subject is not None:
            safe.append({"code": None, "name": str(subject)})
    return safe


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_SAO_PAULO)


def _normalize_step_text(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ")
    text = _LEADING_STEP_NUMBER_RE.sub("", text, count=1)
    text = _ELETRONICA_REFER_RE.sub("ELETRÔNICA REFER", text)
    text = _GLUE_BOUNDARY_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return _PERSONAL_ID_RE.sub("[documento removido]", text)


def _source_step_number(step: dict[str, Any]) -> int | None:
    for key in _SOURCE_STEP_NUMBER_KEYS:
        value = step.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.isdigit():
                return int(candidate)
    return None


def extract_attachment_references(process: dict[str, Any]) -> list[JuditAttachmentRef]:
    secrecy_level = int(process.get("secrecy_level") or process.get("secrecyLevel") or 0)
    if secrecy_level > 0:
        return []
    try:
        instance = int(process.get("instance"))
    except (TypeError, ValueError):
        return []
    if instance <= 0:
        return []

    raw_attachments = process.get("attachments")
    if not isinstance(raw_attachments, list):
        return []

    refs: list[JuditAttachmentRef] = []
    seen: set[str] = set()
    for raw in raw_attachments:
        if not isinstance(raw, dict):
            continue
        attachment_id = str(raw.get("attachment_id") or "").strip()
        if not attachment_id or attachment_id in seen:
            continue
        seen.add(attachment_id)
        status = str(raw.get("status") or "pending").strip().lower() or "pending"
        extension = str(raw.get("extension") or "").strip() or None
        step_id = str(raw.get("step_id") or "").strip() or None
        refs.append(
            JuditAttachmentRef(
                attachment_id=attachment_id,
                instance=instance,
                status=status,
                extension=extension,
                step_id=step_id,
            )
        )
    return refs


def extract_promotable_fields(process: dict[str, Any]) -> dict[str, Any]:
    classifications = process.get("classifications") or []
    class_name = None
    class_code = None
    if classifications and isinstance(classifications[0], dict):
        class_name = classifications[0].get("name")
        class_code = classifications[0].get("code")
    class_name = class_name or process.get("class_name") or process.get("class")

    courts = process.get("courts") or []
    court = process.get("tribunal_acronym") or process.get("court")
    if not court and courts and isinstance(courts[0], dict):
        court = courts[0].get("name") or courts[0].get("code")

    secrecy_level = int(process.get("secrecy_level") or process.get("secrecyLevel") or 0)
    raw_code = process.get("code") or process.get("process_code")
    code = None
    if raw_code:
        try:
            code = normalize_cnj(str(raw_code))
        except ValueError:
            code = str(raw_code).strip() or None

    header_keys = _SECRET_HEADER_KEYS if secrecy_level > 0 else _PUBLIC_HEADER_KEYS
    header = {
        key: process.get(key)
        for key in header_keys
        if process.get(key) is not None
    }
    if secrecy_level == 0 and class_code is not None:
        rendered_class_code = str(class_code).strip()
        if rendered_class_code:
            header["class_code"] = rendered_class_code

    if secrecy_level > 0:
        return {
            "header": header,
            "parties": [],
            "subjects": [],
            "steps": [],
            "court": court,
            "class_name": class_name,
            "secrecy_level": secrecy_level,
        }

    steps = process.get("steps") or process.get("movements") or process.get("events") or []
    normalized_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        step_date = (
            step.get("step_date")
            or step.get("occurred_at")
            or step.get("date")
            or step.get("datetime")
        )
        step_type = step.get("step_type") or step.get("title") or step.get("type")
        raw_text = step.get("content") or step.get("text") or step.get("description") or ""
        occurred_at = _parse_datetime(step_date)
        normalized_steps.append(
            {
                "step_number": index,
                "occurred_at": occurred_at,
                "title": step_type,
                "text": _normalize_step_text(raw_text),
                "metadata": {
                    "cnj": code,
                    "instance": process.get("instance"),
                    "court": court,
                    "type": step_type,
                    "step_id": step.get("step_id"),
                    "step_number": index,
                    "source_step_number": _source_step_number(step),
                    "private": step.get("private"),
                    "secrecy_level": secrecy_level,
                    "tags": step.get("tags") or {},
                    "source_step_date": step_date,
                    "occurred_at_sao_paulo": occurred_at.isoformat() if occurred_at else None,
                },
            }
        )

    return {
        "header": header,
        "parties": _safe_parties(process),
        "subjects": _safe_subjects(process),
        "steps": normalized_steps,
        "court": court,
        "class_name": class_name,
        "secrecy_level": secrecy_level,
    }
