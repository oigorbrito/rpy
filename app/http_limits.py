from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

from starlette.responses import JSONResponse

DEFAULT_JUDIT_WEBHOOK_MAX_BODY_BYTES = 5 * 1024 * 1024


def judit_webhook_max_body_bytes() -> int:
    raw = os.getenv(
        "JUDIT_WEBHOOK_MAX_BODY_BYTES",
        str(DEFAULT_JUDIT_WEBHOOK_MAX_BODY_BYTES),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("JUDIT_WEBHOOK_MAX_BODY_BYTES must be an integer") from exc
    if value <= 0:
        raise RuntimeError("JUDIT_WEBHOOK_MAX_BODY_BYTES must be greater than zero")
    return value


class _RequestBodyTooLarge(Exception):
    pass


class InboundPostBodyLimitMiddleware:
    """Bound inbound POST bodies even when Content-Length is absent or forged."""

    def __init__(self, app: Callable[..., Awaitable[Any]]) -> None:
        self.app = app
        self.limit = judit_webhook_max_body_bytes()

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if (
            scope.get("type") != "http"
            or str(scope.get("method") or "").upper() != "POST"
        ):
            await self.app(scope, receive, send)
            return

        raw_content_length = next(
            (
                value
                for key, value in scope.get("headers", [])
                if key.lower() == b"content-length"
            ),
            None,
        )
        if raw_content_length is not None:
            try:
                content_length = int(raw_content_length)
            except (TypeError, ValueError):
                content_length = None
            if content_length is not None and content_length > self.limit:
                response = JSONResponse(
                    {"detail": "request body too large"},
                    status_code=413,
                )
                await response(scope, receive, send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            response = JSONResponse(
                {"detail": "request body too large"},
                status_code=413,
            )
            await response(scope, receive, send)


# Backward-compatible name retained for tests/importers while the middleware now
# protects every inbound POST at the same application-level byte boundary.
JuditWebhookBodyLimitMiddleware = InboundPostBodyLimitMiddleware
