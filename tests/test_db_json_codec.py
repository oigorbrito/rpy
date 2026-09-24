from __future__ import annotations

import json

from app.db import _encode_json


def test_encode_json_serializes_native_values_once() -> None:
    encoded = _encode_json({"a": 1, "nested": [True, None]})
    assert json.loads(encoded) == {"a": 1, "nested": [True, None]}


def test_encode_json_preserves_already_serialized_json() -> None:
    raw = '{"a":1,"nested":[true]}'
    assert _encode_json(raw) == raw


def test_encode_json_serializes_plain_strings() -> None:
    assert _encode_json("not-json") == '"not-json"'


def test_encode_json_wraps_ambiguous_pre_serialized_json_as_plain_string() -> None:
    for raw in (
        '{"a":1,"a":2}',
        '{"value":NaN}',
        '{"value":Infinity}',
    ):
        encoded = _encode_json(raw)
        decoded = json.loads(encoded)
        if decoded != raw:
            raise AssertionError(
                f"expected ambiguous JSON to be preserved as a string, got {decoded!r}"
            )
