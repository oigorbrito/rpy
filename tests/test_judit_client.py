from __future__ import annotations

import json
import urllib.error
from uuid import uuid4

import pytest

from app import judit_client


class _Headers:
    def __init__(self, values: dict[str, str] | None = None):
        self.values = values or {}

    def get(self, name: str):
        return self.values.get(name)


class _Response:
    status = 201

    def __init__(
        self,
        body: bytes,
        *,
        status: int = 201,
        headers: dict[str, str] | None = None,
    ):
        self.body = body
        self.status = status
        self.headers = _Headers(headers)

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


def test_download_attachment_uses_authenticated_bounded_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "attachment-key")
    monkeypatch.setenv("ATTACHMENT_MAX_BYTES", "16")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(
            b"%PDF-synthetic",
            status=200,
            headers={"Content-Type": "application/pdf; charset=binary"},
        ),
    )

    result = judit_client._download_attachment_sync(
        "0000000-00.0000.0.00.0001",
        1,
        "att/id 123",
    )

    request = captured["request"]
    assert request.full_url == (
        f"{judit_client.JUDIT_LAWSUITS_URL}/"
        "0000000-00.0000.0.00.0001/1/attachments/att%2Fid%20123"
    )
    assert request.method == "GET"
    assert request.get_header("Api-key") == "attachment-key"
    assert result.content_type == "application/pdf"
    assert result.data == b"%PDF-synthetic"


def test_download_attachment_rejects_oversized_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "attachment-key")
    monkeypatch.setenv("ATTACHMENT_MAX_BYTES", "4")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"12345", status=200),
    )

    with pytest.raises(judit_client.JuditRequestError, match="exceeded safe size"):
        judit_client._download_attachment_sync(
            "0000000-00.0000.0.00.0001",
            1,
            "att-1",
        )


def test_download_attachment_http_error_discards_body_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", "super-secret-attachment-key")

    def fail(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "forbidden sensitive provider body",
            {},
            None,
        )

    monkeypatch.setattr(judit_client.urllib.request, "urlopen", fail)

    with pytest.raises(judit_client.JuditRequestError) as exc:
        judit_client._download_attachment_sync(
            "0000000-00.0000.0.00.0001",
            1,
            "att-1",
        )

    assert "403" in str(exc.value)
    assert "super-secret-attachment-key" not in str(exc.value)
    assert "sensitive provider body" not in str(exc.value)
    assert exc.value.retry_safe is True


def test_download_attachment_requires_safe_identifiers() -> None:
    with pytest.raises(ValueError, match="process code"):
        judit_client._download_attachment_sync("", 1, "att-1")
    with pytest.raises(ValueError, match="instance"):
        judit_client._download_attachment_sync("0000000-00.0000.0.00.0001", "", "att-1")
    with pytest.raises(ValueError, match="attachment_id"):
        judit_client._download_attachment_sync("0000000-00.0000.0.00.0001", 1, "")


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
        "with_attachments": False,
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
    with pytest.raises(RuntimeError, match="finite number between 0 and 60"):
        judit_client._timeout_seconds()


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "+inf", "-inf"])
def test_timeout_rejects_non_finite_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("JUDIT_TIMEOUT_SECONDS", value)
    with pytest.raises(RuntimeError, match="finite number between 0 and 60"):
        judit_client._timeout_seconds()


def test_attachment_max_bytes_is_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTACHMENT_MAX_BYTES", "0")
    with pytest.raises(RuntimeError, match="greater than zero"):
        judit_client._attachment_max_bytes()



def test_attachments_require_explicit_enabled_direct_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "true")
    monkeypatch.delenv("JUDIT_ATTACHMENT_DOWNLOAD_MODE", raising=False)
    with pytest.raises(RuntimeError, match="direct_api_key"):
        judit_client.judit_attachments_enabled()

    monkeypatch.setenv("JUDIT_ATTACHMENT_DOWNLOAD_MODE", "direct_api_key")
    assert judit_client.judit_attachments_enabled() is True


def test_create_request_enables_attachments_only_after_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setenv("JUDIT_API_KEY", "attachment-key")
    monkeypatch.setenv("JUDIT_ATTACHMENTS_ENABLED", "true")
    monkeypatch.setenv("JUDIT_ATTACHMENT_DOWNLOAD_MODE", "direct_api_key")
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda request, timeout: captured.update(request=request, timeout=timeout)
        or _Response(b'{"request_id":"req-att"}'),
    )

    result = judit_client._create_request_sync("0000000-00.0000.0.00.0001")

    assert result.request_id == "req-att"
    assert json.loads(captured["request"].data)["with_attachments"] is True


def test_judit_response_rejects_duplicate_json_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUDIT_API_KEY", uuid4().hex)
    monkeypatch.setattr(
        judit_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            b'{"request_id":"first","request_id":"second"}'
        ),
    )
    with pytest.raises(judit_client.JuditRequestError, match="invalid response"):
        judit_client._create_request_sync("0000000-00.0000.0.00.0001")
