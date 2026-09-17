from __future__ import annotations

import json
import urllib.error

import pytest

from app import judit_client


class _Response:
    status = 201

    def __init__(self, body: bytes, *, status: int = 201):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit: int):
        return self.body


def test_create_request_uses_cnj_contract_without_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "secret-provider-key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b'{"request_id":"req-123"}'),
    )
    result = judit_client._create_request_sync("0000000-00.0000.0.00.0001")
    request = captured["request"]
    assert result.request_id == "req-123"
    assert request.full_url == judit_client.JUDIT_REQUESTS_URL
    assert request.method == "POST"
    assert request.get_header("Api-key") == "secret-provider-key"
    assert json.loads(request.data) == {
        "search": {"search_type": "lawsuit_cnj", "search_key": "0000000-00.0000.0.00.0001"},
        "with_attachments": False,
    }


def test_create_tracking_uses_lawsuit_cnj_and_recurrence(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "tracking-key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b'{"tracking_id":"track-123","status":"created"}', status=201),
    )

    result = judit_client._create_tracking_sync("0000000-00.0000.0.00.0001", 2)

    request = captured["request"]
    assert result.tracking_id == "track-123"
    assert result.status == "created"
    assert request.full_url == judit_client.JUDIT_TRACKING_URL
    assert request.method == "POST"
    assert json.loads(request.data) == {
        "recurrence": 2,
        "search": {
            "search_type": "lawsuit_cnj",
            "search_key": "0000000-00.0000.0.00.0001",
            "response_type": "lawsuit",
        },
    }


def test_delete_tracking_is_idempotent_on_provider_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "tracking-key")

    def missing(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, None)

    monkeypatch.setattr(judit_client.urllib.request, "urlopen", missing)
    judit_client._delete_tracking_sync("track-123")


def test_provider_error_discards_body_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "secret-provider-key")

    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError(judit_client.JUDIT_REQUESTS_URL, 401, "bad", {}, None)

    monkeypatch.setattr(judit_client.urllib.request, "urlopen", fail)
    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._create_request_sync("0000000-00.0000.0.00.0001")
    assert "401" in str(exc.value)
    assert "secret-provider-key" not in str(exc.value)
    assert exc.value.retry_safe is True


@pytest.mark.parametrize("status", [408, 429, 500, 502])
def test_ambiguous_judit_http_failures_are_not_retry_safe(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "key")

    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError(judit_client.JUDIT_REQUESTS_URL, status, "failed", {}, None)

    monkeypatch.setattr(judit_client.urllib.request, "urlopen", fail)
    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._create_request_sync("0000000-00.0000.0.00.0001")
    assert exc.value.retry_safe is False


def test_transport_failure_is_not_retry_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
    )
    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._create_request_sync("0000000-00.0000.0.00.0001")
    assert exc.value.retry_safe is False


def test_missing_request_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"{}"),
    )
    with pytest.raises(judit_client.JuditRequestError, match="missing request id"):
        judit_client._create_request_sync("0000000-00.0000.0.00.0001")


def test_missing_tracking_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"{}", status=201),
    )
    with pytest.raises(judit_client.JuditRequestError, match="missing tracking id"):
        judit_client._create_tracking_sync("0000000-00.0000.0.00.0001", 1)


def test_tracking_recurrence_must_be_positive() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        judit_client._create_tracking_sync("0000000-00.0000.0.00.0001", 0)


def test_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDIT_TIMEOUT_SECONDS", "61")
    with pytest.raises(RuntimeError, match="between 0 and 60"):
        judit_client._timeout_seconds()
