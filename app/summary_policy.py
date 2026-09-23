from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.json_utils import decode_json_object
from app.summary_output import structured_summary_document_is_canonical

RESTRICTED_MODEL = "local-deterministic"
RESTRICTED_PROMPT_VERSION = "secret-summary-v1"
RESTRICTED_HEADER_FIELDS = (
    ("instance", "Instância"),
    ("area", "Área"),
    ("justice_description", "Justiça"),
    ("county", "Comarca"),
    ("state", "Estado"),
    ("city", "Cidade"),
)


def _inline(value: Any) -> str:
    return " ".join(str(value or "").split())


def is_restricted_local_summary(
    row: Mapping[str, Any],
    *,
    process: Mapping[str, Any],
) -> bool:
    if (
        row["model"] != RESTRICTED_MODEL
        or row["prompt_version"] != RESTRICTED_PROMPT_VERSION
    ):
        return False
    try:
        structured_output = decode_json_object(
            row["structured_output"],
            label="restricted summary structured output",
        )
        process_header = decode_json_object(
            process["header"],
            label="restricted process header",
        )
        expected_process = {
            "cnj": _inline(process["code"]),
            "class_name": _inline(process["class_name"]) or None,
            "court": None,
            "header": restricted_public_header(process_header),
            "parties": [],
        }
    except (KeyError, TypeError, ValueError):
        return False
    return (
        structured_summary_document_is_canonical(structured_output)
        and structured_output["process"] == expected_process
    )


def restricted_public_header(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value[key]
        for key, _ in RESTRICTED_HEADER_FIELDS
        if key in value and value[key] is not None
    }
