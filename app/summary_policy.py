from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RESTRICTED_MODEL = "local-deterministic"
RESTRICTED_PROMPT_VERSION = "restricted-summary-v1"
RESTRICTED_HEADER_FIELDS = (
    ("instance", "Instância"),
    ("area", "Área"),
    ("justice_description", "Justiça"),
    ("county", "Comarca"),
    ("state", "Estado"),
    ("city", "Cidade"),
)


def is_restricted_local_summary(row: Mapping[str, Any]) -> bool:
    return (
        row["model"] == RESTRICTED_MODEL
        and row["prompt_version"] == RESTRICTED_PROMPT_VERSION
    )


def restricted_public_header(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value[key]
        for key, _ in RESTRICTED_HEADER_FIELDS
        if key in value and value[key] is not None
    }
