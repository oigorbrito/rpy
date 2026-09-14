from __future__ import annotations

import json
from typing import Any


def decode_json_object(value: Any, *, label: str = "JSON value") -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise ValueError(f"{label} must decode to an object")
        return decoded
    try:
        decoded = dict(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a JSON object") from exc
    return decoded
