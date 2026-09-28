from __future__ import annotations

import json

from scripts.external_connectivity_smoke import PROBE_BODY, PROBE_HEADER, run_echo_probe


class _Response:
    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self._payload if amount < 0 else self._payload[:amount]


def _urlopen_with(payload: dict):
    raw = json.dumps(payload).encode("utf-8")

    def _urlopen(request, *, timeout: float):
        assert request.full_url == "https://postman-echo.com/post"
        assert request.method == "POST"
        assert timeout > 0
        assert json.loads(request.data.decode("utf-8")) == PROBE_BODY
        return _Response(raw)

    return _urlopen


def test_echo_probe_proves_json_and_header_roundtrip() -> None:
    report = run_echo_probe(
        urlopen=_urlopen_with(
            {
                "json": PROBE_BODY,
                "headers": {
                    "content-type": "application/json",
                    "x-rpy-probe": PROBE_HEADER,
                },
            }
        )
    )

    assert report["status"] == "ok"
    assert report["http_status"] == 200
    assert report["json_roundtrip"] is True
    assert report["probe_header_roundtrip"] is True
    assert report["content_type_roundtrip"] is True
    assert report["error_code"] is None


def test_echo_probe_detects_contract_mismatch() -> None:
    report = run_echo_probe(
        urlopen=_urlopen_with(
            {
                "json": {"probe": "wrong"},
                "headers": {
                    "content-type": "application/json",
                    "x-rpy-probe": "wrong",
                },
            }
        )
    )

    assert report["status"] == "mismatch"
    assert report["error_code"] == "echo_contract_mismatch"


def test_echo_probe_rejects_invalid_json() -> None:
    def _urlopen(request, *, timeout: float):
        return _Response(b"not-json")

    report = run_echo_probe(urlopen=_urlopen)

    assert report["status"] == "error"
    assert report["error_code"] == "invalid_json"


def test_echo_probe_rejects_oversized_response() -> None:
    def _urlopen(request, *, timeout: float):
        return _Response(b"x" * (64 * 1024 + 1))

    report = run_echo_probe(urlopen=_urlopen)

    assert report["status"] == "error"
    assert report["error_code"] == "response_too_large"
