from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

JUDIT_REQUESTS_URL = "https://requests.production.judit.io/requests/"


class JuditRequestError(RuntimeError):
    """Safe provider-boundary failure; response bodies are deliberately discarded."""

    def __init__(self, message: str, *, retry_safe: bool = False) -> None:
        super().__init__(message)
        self.retry_safe = retry_safe


@dataclass(frozen=True, slots=True)
class JuditRequestResult:
    request_id: str


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

def _callback_url() -> str:
    value = os.environ.get("JUDIT_CALLBACK_URL", "").strip()
    if not value:
        raise RuntimeError("JUDIT_CALLBACK_URL is required")
    if not value.startswith("https://"):
        raise RuntimeError("JUDIT_CALLBACK_URL must use https")
    return value

def _create_request_sync(code: str) -> JuditRequestResult:
    payload = json.dumps(
        {
            "search": {"search_type": "lawsuit_cnj", "search_key": code},
            "with_attachments": False,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        JUDIT_REQUESTS_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "api-key": _api_key(),
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            if response.status != 201:
                raise JuditRequestError(f"Judit request failed with HTTP {response.status}")
            raw = response.read(262145)
            if len(raw) > 262144:
                raise JuditRequestError("Judit response exceeded safe size")
    except urllib.error.HTTPError as exc:
        # A 4xx response is an explicit rejection: the provider did not accept a
        # valid asynchronous request, so a later explicit retry is safe. 5xx and
        # transport failures remain ambiguous and must never be retried blindly.
        raise JuditRequestError(
            f"Judit request failed with HTTP {exc.code}", retry_safe=400 <= exc.code < 500
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise JuditRequestError("Judit request failed") from None

    try:
        body: Any = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise JuditRequestError("Judit returned an invalid response") from None

    request_id = body.get("request_id") if isinstance(body, dict) else None
    if not isinstance(request_id, str) or not request_id.strip():
        raise JuditRequestError("Judit response missing request id")
    return JuditRequestResult(request_id=request_id.strip())


async def create_lawsuit_request(code: str) -> JuditRequestResult:
    return await asyncio.to_thread(_create_request_sync, code)
