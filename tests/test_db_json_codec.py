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
