from __future__ import annotations

import json
from typing import Any


def decode_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def decode_json_object(value: Any, *, label: str = "JSON value") -> dict[str, Any]:
    if value is None:
        return {}
    decoded = decode_json_value(value)
    if isinstance(decoded, dict):
        return dict(decoded)
    try:
        return dict(decoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a JSON object") from exc


def decode_json_list(value: Any, *, label: str = "JSON value") -> list[Any]:
    if value is None:
        return []
    decoded = decode_json_value(value)
    if isinstance(decoded, list):
        return list(decoded)
    if isinstance(decoded, tuple):
        return list(decoded)
    raise ValueError(f"{label} must be a JSON array")
