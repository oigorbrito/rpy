from __future__ import annotations

import pytest

from app.json_utils import loads_strict_json


def test_strict_json_accepts_nested_standard_json() -> None:
    parsed = loads_strict_json(
        b'{"outer":{"items":[1,2,3],"enabled":true},"name":"ok"}'
    )
    if parsed != {
        "outer": {"items": [1, 2, 3], "enabled": True},
        "name": "ok",
    }:
        raise AssertionError(f"unexpected strict JSON parse result: {parsed!r}")


@pytest.mark.parametrize(
    "payload",
    [
        b'{"token":"first","token":"second"}',
        b'{"outer":{"id":1,"id":2}}',
    ],
)
def test_strict_json_rejects_duplicate_object_keys(payload: bytes) -> None:
    with pytest.raises(ValueError, match="duplicate key"):
        loads_strict_json(payload)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_strict_json_rejects_non_standard_numeric_constants(constant: str) -> None:
    with pytest.raises(ValueError, match="non-standard numeric constant"):
        loads_strict_json(f'{{"value":{constant}}}')
