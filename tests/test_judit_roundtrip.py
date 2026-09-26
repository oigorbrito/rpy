from __future__ import annotations

import json
import urllib.parse

import pytest

from app import judit_client


class _Response:
    def __init__(self, body: bytes, *, status: int = 200):
        self.body = body
        self.status = status
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit: int):
        return self.body


def test_request_status_uses_authenticated_non_creating_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "roundtrip-key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b'{"status":"completed"}'),
    )

    result = judit_client._get_request_status_sync("req/id ?#")

    request = captured["request"]
    assert request.method == "GET"
    assert request.data is None
    assert request.get_header("Api-key") == "roundtrip-key"
    assert request.full_url == (
        f"{judit_client.JUDIT_REQUESTS_BASE_URL}/requests/req%2Fid%20%3F%23"
    )
    assert result.status == "completed"


def test_request_status_rejects_missing_or_unsafe_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "roundtrip-key")

    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"{}"),
    )
    with pytest.raises(judit_client.JuditRequestError, match="missing status"):
        judit_client._get_request_status_sync("req-1")

    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            json.dumps({"status": "completed with sensitive free text"}).encode()
        ),
    )
    with pytest.raises(judit_client.JuditRequestError, match="unsafe status"):
        judit_client._get_request_status_sync("req-1")


def test_responses_query_is_request_scoped_and_returns_only_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "roundtrip-key")
    body = {
        "page": 1,
        "page_data": [
            {
                "request_id": "req-1",
                "response_id": "resp-1",
                "response_type": "lawsuit",
                "response_data": {"code": "sensitive-process-data"},
            },
            {
                "request_id": "req-1",
                "response_id": "resp-2",
                "response_type": "entity",
                "response_data": {"document": "sensitive-document"},
            },
        ],
    }
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(json.dumps(body).encode()),
    )

    result = judit_client._get_responses_sync("req/id ?#")

    request = captured["request"]
    assert request.method == "GET"
    assert request.data is None
    parsed = urllib.parse.urlsplit(request.full_url)
    assert parsed.path == "/responses"
    assert urllib.parse.parse_qs(parsed.query) == {"request_id": ["req/id ?#"]}
    assert result.response_count == 2
    assert result.lawsuit_response_count == 1
    assert not hasattr(result, "response_data")


def test_responses_rejects_payload_without_page_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "roundtrip-key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b'{"page":1}'),
    )

    with pytest.raises(judit_client.JuditRequestError, match="missing page_data"):
        judit_client._get_responses_sync("req-1")


@pytest.mark.parametrize("request_id", ["", " ", "\t", None, 123])
def test_roundtrip_reads_require_request_id(request_id) -> None:
    with pytest.raises(ValueError, match="request_id is required"):
        judit_client._get_request_status_sync(request_id)
    with pytest.raises(ValueError, match="request_id is required"):
        judit_client._get_responses_sync(request_id)
