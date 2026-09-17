from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

JUDIT_REQUESTS_URL = "https://requests.production.judit.io/requests/"
JUDIT_TRACKING_URL = "https://tracking.production.judit.io/tracking"
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
            "with_attachments": False,
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


async def create_lawsuit_tracking(code: str, *, recurrence_days: int = 1) -> JuditTrackingResult:
    return await asyncio.to_thread(_create_tracking_sync, code, recurrence_days)


async def delete_lawsuit_tracking(tracking_id: str) -> None:
    await asyncio.to_thread(_delete_tracking_sync, tracking_id)
