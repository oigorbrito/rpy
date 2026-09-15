from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

_CNJ_CANONICAL_RE = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")
_CNJ_DIGITS_RE = re.compile(r"^\d{20}$")


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


def _safe_parties(process: dict[str, Any]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for party in process.get("parties") or []:
        if not isinstance(party, dict):
            continue
        name = str(party.get("name") or "").strip()
        if not name:
            continue
        safe.append(
            {
                "name": name,
                "side": party.get("side"),
                "person_type": party.get("person_type"),
            }
        )
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
        return value
    if isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


def extract_promotable_fields(process: dict[str, Any]) -> dict[str, Any]:
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
        normalized_steps.append(
            {
                "step_number": index,
                "occurred_at": _parse_datetime(step_date),
                "title": step.get("step_type") or step.get("title") or step.get("type"),
                "text": step.get("content") or step.get("text") or step.get("description") or "",
                "metadata": {
                    "step_id": step.get("step_id"),
                    "private": step.get("private"),
                    "tags": step.get("tags") or {},
                    "source_step_date": step_date,
                },
            }
        )

    classifications = process.get("classifications") or []
    class_name = None
    if classifications and isinstance(classifications[0], dict):
        class_name = classifications[0].get("name")
    class_name = class_name or process.get("class_name") or process.get("class")

    courts = process.get("courts") or []
    court = process.get("tribunal_acronym") or process.get("court")
    if not court and courts and isinstance(courts[0], dict):
        court = courts[0].get("name") or courts[0].get("code")

    header = {
        key: process.get(key)
        for key in (
            "name",
            "instance",
            "area",
            "justice_description",
            "county",
            "state",
            "city",
            "amount",
        )
        if process.get(key) is not None
    }

    return {
        "header": header,
        "parties": _safe_parties(process),
        "subjects": _safe_subjects(process),
        "steps": normalized_steps,
        "court": court,
        "class_name": class_name,
        "secrecy_level": int(process.get("secrecy_level") or process.get("secrecyLevel") or 0),
    }
