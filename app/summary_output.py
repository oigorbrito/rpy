from __future__ import annotations

import json
import re
from typing import Any

SUMMARY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "synthesis": {"type": "string", "minLength": 1},
        "timeline": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 24,
        },
        "current_status": {"type": "string", "minLength": 1},
        "attention": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 12,
        },
        "decisions": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 16,
        },
        "deadlines": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 12,
        },
        "related_processes": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 12,
        },
        "attachments": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 12,
        },
    },
    "required": [
        "synthesis",
        "timeline",
        "current_status",
        "attention",
        "decisions",
        "deadlines",
        "related_processes",
        "attachments",
    ],
    "additionalProperties": False,
}

_HEADER_FIELDS = (
    ("instance", "Instância"),
    ("area", "Área"),
    ("justice_description", "Justiça"),
    ("county", "Comarca"),
    ("state", "Estado"),
    ("city", "Cidade"),
    ("amount", "Valor"),
)
_LIST_FIELDS = (
    ("timeline", "Linha do tempo relevante"),
    ("decisions", "Decisões"),
    ("deadlines", "Prazos em curso"),
    ("related_processes", "Processos relacionados"),
    ("attachments", "Anexos"),
)
_REQUIRED_KEYS = frozenset(SUMMARY_OUTPUT_SCHEMA["required"])
_SPACE_RE = re.compile(r"\s+")


def _inline(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def _prose_line(value: Any) -> str:
    text = _inline(value)
    if text.startswith(("#", "<", "`", "- ", "* ")):
        return "\u2060" + text
    return text


def _string_list(value: Any, *, key: str, require_nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"structured summary field must be a string array: {key}")
    rendered = [_prose_line(item) for item in value if _inline(item)]
    if require_nonempty and not rendered:
        raise ValueError(f"structured summary field must not be empty: {key}")
    return rendered


def parse_structured_summary(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("provider structured summary is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("provider structured summary must be a JSON object")
    if set(payload) != _REQUIRED_KEYS:
        raise ValueError("provider structured summary keys do not match the contract")

    synthesis = payload.get("synthesis")
    current_status = payload.get("current_status")
    if not isinstance(synthesis, str) or not _inline(synthesis):
        raise ValueError("structured summary synthesis must be a non-empty string")
    if not isinstance(current_status, str) or not _inline(current_status):
        raise ValueError("structured summary current_status must be a non-empty string")

    normalized = {
        "synthesis": _prose_line(synthesis),
        "timeline": _string_list(payload.get("timeline"), key="timeline"),
        "current_status": _prose_line(current_status),
        "attention": _string_list(
            payload.get("attention"), key="attention", require_nonempty=True
        ),
        "decisions": _string_list(payload.get("decisions"), key="decisions"),
        "deadlines": _string_list(payload.get("deadlines"), key="deadlines"),
        "related_processes": _string_list(
            payload.get("related_processes"), key="related_processes"
        ),
        "attachments": _string_list(payload.get("attachments"), key="attachments"),
    }
    return normalized


def _append_list(lines: list[str], title: str, items: list[str]) -> None:
    if not items:
        return
    lines.extend(["", f"## {title}", *(f"- {item}" for item in items)])


def render_structured_summary(payload: dict[str, Any], context: dict[str, Any]) -> str:
    lines = ["# Resumo do processo", "", '<ProcessHeader className="process-header">']
    lines.append(f"- Processo: {_inline(context.get('code'))}")

    class_name = _inline(context.get("class_name"))
    if class_name:
        lines.append(f"- Classe: {class_name}")
    court = _inline(context.get("court"))
    if court:
        lines.append(f"- Tribunal: {court}")

    header = context.get("header") if isinstance(context.get("header"), dict) else {}
    for key, label in _HEADER_FIELDS:
        rendered = _inline(header.get(key))
        if rendered:
            lines.append(f"- {label}: {rendered}")
    lines.append("</ProcessHeader>")

    parties = context.get("parties")
    if isinstance(parties, list):
        party_names = [
            _inline(item.get("name") or item.get("nome"))
            for item in parties
            if isinstance(item, dict)
        ]
        party_names = [name for name in party_names if name]
        if party_names:
            lines.extend(["", "## Partes", *(f"- {name}" for name in party_names)])

    lines.extend(["", "## Síntese", payload["synthesis"]])
    _append_list(lines, "Linha do tempo relevante", payload["timeline"])
    lines.extend(["", "## Situação atual", payload["current_status"]])
    lines.extend(
        ["", "## Pontos de atenção", *(f"- {item}" for item in payload["attention"])]
    )

    for key, title in _LIST_FIELDS[1:]:
        _append_list(lines, title, payload[key])

    return "\n".join(lines).strip()
