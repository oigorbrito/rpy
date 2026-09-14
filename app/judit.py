from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class JuditEvent:
    request_id: str | None
    code: str
    cached_response: bool
    request_completed: bool
    raw: dict[str, Any]


def parse_event(payload: dict[str, Any]) -> JuditEvent:
    code = str(
        payload.get("code")
        or payload.get("process_code")
        or payload.get("processNumber")
        or ""
    ).strip()
    if not code:
        raise ValueError("missing process code")

    request_id = payload.get("request_id") or payload.get("requestId") or payload.get("id")
    status = str(payload.get("status") or "").lower()
    request_completed = bool(
        payload.get("request_completed")
        or payload.get("requestCompleted")
        or status == "request_completed"
        or status == "completed"
    )
    cached_response = bool(payload.get("cached_response") or payload.get("cachedResponse"))
    return JuditEvent(
        request_id=str(request_id) if request_id is not None else None,
        code=code,
        cached_response=cached_response,
        request_completed=request_completed,
        raw=payload,
    )


def extract_promotable_fields(payload: dict[str, Any]) -> dict[str, Any]:
    process = payload.get("process") if isinstance(payload.get("process"), dict) else payload
    steps = process.get("steps") or process.get("movements") or process.get("events") or []
    normalized_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        normalized_steps.append(
            {
                "step_number": int(step.get("step_number") or step.get("number") or index),
                "occurred_at": step.get("occurred_at") or step.get("date") or step.get("datetime"),
                "title": step.get("title") or step.get("type"),
                "text": step.get("text") or step.get("description") or step.get("content") or "",
                "metadata": step,
            }
        )
    return {
        "header": process.get("header") or {},
        "parties": process.get("parties") or [],
        "subjects": process.get("subjects") or process.get("subject") or [],
        "steps": normalized_steps,
        "court": process.get("court"),
        "class_name": process.get("class_name") or process.get("class"),
        "secrecy_level": int(process.get("secrecy_level") or process.get("secrecyLevel") or 0),
    }
