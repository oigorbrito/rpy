from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings, strategies as st

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


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", ["/extra", "/", "/ação"])
async def test_malformed_webhook_path_redacts_token_without_making_route_valid(
    tail: str,
) -> None:
    seen: dict[str, Any] = {}

    async def app(scope, receive, send) -> None:
        seen["path"] = scope["path"]
        seen["raw_path"] = scope["raw_path"]
        seen["state"] = dict(scope.get("state") or {})

    middleware = JuditWebhookSecretRedactionMiddleware(app)
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/webhooks/judit/super-secret-token{tail}",
        "raw_path": f"/webhooks/judit/super-secret-token{tail}".encode(),
        "state": {},
    }

    await middleware(scope, None, None)

    assert seen["path"] == f"/webhooks/judit/{REDACTED_WEBHOOK_TOKEN}{tail}"
    assert b"super-secret-token" not in seen["raw_path"]
    assert JUDIT_WEBHOOK_TOKEN_STATE_KEY not in seen["state"]
    assert webhook_token_from_scope(scope, REDACTED_WEBHOOK_TOKEN) == REDACTED_WEBHOOK_TOKEN


_TOKEN_ALPHABET = st.characters(
    blacklist_characters="/\\",
    blacklist_categories=("Cs", "Cc"),
)
_TOKEN_STRATEGY = st.text(_TOKEN_ALPHABET, min_size=1, max_size=128)


@settings(max_examples=128, deadline=None)
@given(token=_TOKEN_STRATEGY)
@pytest.mark.asyncio
async def test_webhook_redaction_property_never_exposes_valid_route_token(token: str) -> None:
    seen: dict[str, Any] = {}

    async def app(scope, receive, send) -> None:
        seen["path"] = scope["path"]
        seen["raw_path"] = scope["raw_path"]
        seen["state"] = dict(scope.get("state") or {})

    middleware = JuditWebhookSecretRedactionMiddleware(app)
    path = f"/webhooks/judit/{token}"
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "state": {},
    }

    await middleware(scope, None, None)

    redacted_path = f"/webhooks/judit/{REDACTED_WEBHOOK_TOKEN}"
    assert seen["path"] == redacted_path  # nosec B101
    assert seen["raw_path"] == redacted_path.encode("utf-8")  # nosec B101
    assert seen["state"][JUDIT_WEBHOOK_TOKEN_STATE_KEY] == token  # nosec B101
    assert webhook_token_from_scope(scope, REDACTED_WEBHOOK_TOKEN) == token  # nosec B101


@settings(max_examples=96, deadline=None)
@given(token=_TOKEN_STRATEGY, tail=st.sampled_from(("/", "/extra", "/ação", "/%2F")))
@pytest.mark.asyncio
async def test_webhook_redaction_property_malformed_paths_never_gain_auth_state(
    token: str,
    tail: str,
) -> None:
    seen: dict[str, Any] = {}

    async def app(scope, receive, send) -> None:
        seen["path"] = scope["path"]
        seen["raw_path"] = scope["raw_path"]
        seen["state"] = dict(scope.get("state") or {})

    middleware = JuditWebhookSecretRedactionMiddleware(app)
    path = f"/webhooks/judit/{token}{tail}"
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "state": {},
    }

    await middleware(scope, None, None)

    expected_path = f"/webhooks/judit/{REDACTED_WEBHOOK_TOKEN}{tail}"
    assert seen["path"] == expected_path  # nosec B101
    assert seen["raw_path"] == expected_path.encode("utf-8")  # nosec B101
    assert JUDIT_WEBHOOK_TOKEN_STATE_KEY not in seen["state"]  # nosec B101
    assert webhook_token_from_scope(scope, REDACTED_WEBHOOK_TOKEN) == REDACTED_WEBHOOK_TOKEN  # nosec B101
