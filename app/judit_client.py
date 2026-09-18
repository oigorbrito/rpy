from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

JUDIT_REQUESTS_URL = "https://requests.production.judit.io/requests/"
JUDIT_TRACKING_URL = "https://tracking.production.judit.io/tracking"
DEFAULT_JUDIT_LAWSUITS_URL = "https://lawsuits.production.judit.io"
_MAX_RESPONSE_BYTES = 262144


class JuditRequestError(RuntimeError):
    """Safe provider-boundary failure; response bodies are deliberately discarded."""

    def __init__(self, message: str, *, retry_safe: bool = False) -> None:
        super().__init__(message)
        self.retry_safe = retry_safe


@dataclass(frozen=True, slots=True)
class JuditRequestResult:
    request_id: str


@dataclass(frozen=True, slots=True)
class JuditTrackingResult:
    tracking_id: str
    status: str


@dataclass(frozen=True, slots=True)
class JuditAttachmentDownload:
    content_type: str
    data: bytes


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def judit_attachments_enabled() -> bool:
    return _env_bool("JUDIT_ATTACHMENTS_ENABLED", False)


def _lawsuits_base_url() -> str:
    value = str(os.environ.get("JUDIT_LAWSUITS_URL") or DEFAULT_JUDIT_LAWSUITS_URL).strip()
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise RuntimeError("JUDIT_LAWSUITS_URL must be an https URL without embedded credentials")
    return value.rstrip("/")


def _timeout_seconds() -> float:
    raw = os.environ.get("JUDIT_TIMEOUT_SECONDS", "15")
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("JUDIT_TIMEOUT_SECONDS must be numeric") from exc
    if not 0 < value <= 60:
        raise RuntimeError("JUDIT_TIMEOUT_SECONDS must be between 0 and 60")
    return value


def _api_key() -> str:
    value = os.environ.get("JUDIT_API_KEY", "").strip()
    if not value:
        raise RuntimeError("JUDIT_API_KEY is required")
    return value


def _provider_request(
    url: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    accepted_statuses: set[int],
    not_found_is_success: bool = False,
) -> dict[str, Any] | None:
    data = (
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if payload is not None
        else None
    )
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "api-key": _api_key(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            if response.status not in accepted_statuses:
                raise JuditRequestError(f"Judit request failed with HTTP {response.status}")
            if response.status == 204:
                return None
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise JuditRequestError("Judit response exceeded safe size")
    except urllib.error.HTTPError as exc:
        if not_found_is_success and exc.code == 404:
            return None
        # A normal 4xx response is an explicit rejection and can be retried only
        # after an operator/user changes or explicitly repeats the request. 5xx,
        # 408, 429 and transport failures remain ambiguous.
        raise JuditRequestError(
            f"Judit request failed with HTTP {exc.code}",
            retry_safe=400 <= exc.code < 500 and exc.code not in {408, 429},
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise JuditRequestError("Judit request failed") from None

    if not raw:
        return None
    try:
        body: Any = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise JuditRequestError("Judit returned an invalid response") from None
    if not isinstance(body, dict):
        raise JuditRequestError("Judit returned an invalid response")
    return body


def _create_request_sync(code: str) -> JuditRequestResult:
    body = _provider_request(
        JUDIT_REQUESTS_URL,
        method="POST",
        payload={
            "search": {"search_type": "lawsuit_cnj", "search_key": code},
            "with_attachments": judit_attachments_enabled(),
        },
        accepted_statuses={201},
    )
    request_id = body.get("request_id") if body else None
    if not isinstance(request_id, str) or not request_id.strip():
        raise JuditRequestError("Judit response missing request id")
    return JuditRequestResult(request_id=request_id.strip())


def _create_tracking_sync(code: str, recurrence_days: int) -> JuditTrackingResult:
    if recurrence_days <= 0:
        raise ValueError("tracking recurrence_days must be greater than zero")
    body = _provider_request(
        JUDIT_TRACKING_URL,
        method="POST",
        payload={
            "recurrence": recurrence_days,
            "search": {
                "search_type": "lawsuit_cnj",
                "search_key": code,
                "response_type": "lawsuit",
            },
        },
        accepted_statuses={200, 201},
    )
    tracking_id = body.get("tracking_id") if body else None
    if not isinstance(tracking_id, str) or not tracking_id.strip():
        raise JuditRequestError("Judit tracking response missing tracking id")
    status = str(body.get("status") or "created").strip().lower()
    return JuditTrackingResult(tracking_id=tracking_id.strip(), status=status)


def _delete_tracking_sync(tracking_id: str) -> None:
    identifier = str(tracking_id).strip()
    if not identifier:
        raise ValueError("tracking_id is required")
    _provider_request(
        f"{JUDIT_TRACKING_URL}/{identifier}",
        method="DELETE",
        accepted_statuses={200, 204},
        not_found_is_success=True,
    )


async def create_lawsuit_request(code: str) -> JuditRequestResult:
    return await asyncio.to_thread(_create_request_sync, code)


def _attachment_url_sync(code: str, instance: int, attachment_id: str) -> str:
    identifier = str(attachment_id).strip()
    if not identifier:
        raise ValueError("attachment_id is required")
    if instance <= 0:
        raise ValueError("attachment instance must be greater than zero")
    encoded_code = urllib.parse.quote(str(code).strip(), safe="")
    encoded_id = urllib.parse.quote(identifier, safe="")
    body = _provider_request(
        f"{_lawsuits_base_url()}/lawsuits/{encoded_code}/{instance}/attachments/{encoded_id}",
        method="GET",
        accepted_statuses={200},
    )
    value = body.get("attachment_url") if body else None
    if not isinstance(value, str) or not value.strip():
        raise JuditRequestError("Judit attachment response missing download url")
    url = value.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise JuditRequestError("Judit attachment returned an unsafe download url")
    return url


def _sniff_attachment_content_type(raw_type: str | None, data: bytes) -> str:
    media_type = str(raw_type or "").split(";", 1)[0].strip().lower()
    if media_type in {"application/pdf", "text/plain", "image/png", "image/jpeg"}:
        return media_type
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return media_type or "application/octet-stream"
    return "text/plain"


def _download_signed_attachment_sync(url: str, max_bytes: int) -> JuditAttachmentDownload:
    if max_bytes <= 0:
        raise ValueError("attachment max_bytes must be greater than zero")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise JuditRequestError("unsafe signed attachment url")
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "*/*", "User-Agent": "rpy-attachment/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise JuditRequestError("Judit attachment exceeded safe size", retry_safe=True)
            content_type = _sniff_attachment_content_type(
                response.headers.get("Content-Type"),
                raw,
            )
    except urllib.error.HTTPError as exc:
        raise JuditRequestError(
            f"Judit attachment download failed with HTTP {exc.code}",
            retry_safe=400 <= exc.code < 500 and exc.code not in {408, 429},
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise JuditRequestError("Judit attachment download failed") from None
    return JuditAttachmentDownload(content_type=content_type, data=raw)


async def get_lawsuit_attachment_url(code: str, *, instance: int, attachment_id: str) -> str:
    return await asyncio.to_thread(_attachment_url_sync, code, instance, attachment_id)


async def download_signed_attachment(url: str, *, max_bytes: int) -> JuditAttachmentDownload:
    return await asyncio.to_thread(_download_signed_attachment_sync, url, max_bytes)


async def create_lawsuit_tracking(code: str, *, recurrence_days: int = 1) -> JuditTrackingResult:
    return await asyncio.to_thread(_create_tracking_sync, code, recurrence_days)


async def delete_lawsuit_tracking(tracking_id: str) -> None:
    await asyncio.to_thread(_delete_tracking_sync, tracking_id)
