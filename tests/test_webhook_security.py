from __future__ import annotations

from typing import Any

import pytest

from app.webhook_security import (
    JUDIT_WEBHOOK_TOKEN_STATE_KEY,
    REDACTED_WEBHOOK_TOKEN,
    JuditWebhookSecretRedactionMiddleware,
    webhook_token_from_scope,
)


@pytest.mark.asyncio
async def test_judit_webhook_token_is_removed_from_asgi_path() -> None:
    seen: dict[str, Any] = {}

    async def app(scope, receive, send) -> None:
        seen["path"] = scope["path"]
        seen["raw_path"] = scope["raw_path"]
        seen["state"] = dict(scope.get("state") or {})

    middleware = JuditWebhookSecretRedactionMiddleware(app)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/webhooks/judit/super-secret-token",
        "raw_path": b"/webhooks/judit/super-secret-token",
        "state": {},
    }

    await middleware(scope, None, None)

    assert seen["path"] == f"/webhooks/judit/{REDACTED_WEBHOOK_TOKEN}"
    assert b"super-secret-token" not in seen["raw_path"]
    assert seen["state"][JUDIT_WEBHOOK_TOKEN_STATE_KEY] == "super-secret-token"
    assert webhook_token_from_scope(scope, REDACTED_WEBHOOK_TOKEN) == "super-secret-token"


@pytest.mark.asyncio
async def test_non_webhook_path_is_not_rewritten() -> None:
    seen: dict[str, Any] = {}

    async def app(scope, receive, send) -> None:
        seen["path"] = scope["path"]
        seen["raw_path"] = scope["raw_path"]
        seen["state"] = dict(scope.get("state") or {})

    middleware = JuditWebhookSecretRedactionMiddleware(app)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "raw_path": b"/health",
        "state": {},
    }

    await middleware(scope, None, None)

    assert seen["path"] == "/health"
    assert seen["raw_path"] == b"/health"
    assert seen["state"] == {}


def test_webhook_token_falls_back_to_route_value_without_middleware_state() -> None:
    scope = {"type": "http", "state": {}}
    assert webhook_token_from_scope(scope, "route-token") == "route-token"
