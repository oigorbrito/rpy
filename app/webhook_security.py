from __future__ import annotations

from typing import Any

REDACTED_WEBHOOK_TOKEN = "__redacted__"
JUDIT_WEBHOOK_PREFIX = "/webhooks/judit/"
JUDIT_WEBHOOK_TOKEN_STATE_KEY = "judit_webhook_token"


class JuditWebhookSecretRedactionMiddleware:
    """Remove the webhook credential from the ASGI path before app/access logging.

    Judit callbacks authenticate through an opaque URL segment. Keeping the
    credential in ``scope['path']`` allows generic access logs and application
    diagnostics to persist it. The middleware preserves the original token only
    in request-local ASGI state and replaces the visible route value with a
    constant placeholder before the request reaches FastAPI.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if path.startswith(JUDIT_WEBHOOK_PREFIX):
            token = path.removeprefix(JUDIT_WEBHOOK_PREFIX)
            if token and "/" not in token:
                state = scope.setdefault("state", {})
                state[JUDIT_WEBHOOK_TOKEN_STATE_KEY] = token
                redacted_path = JUDIT_WEBHOOK_PREFIX + REDACTED_WEBHOOK_TOKEN
                scope["path"] = redacted_path
                scope["raw_path"] = redacted_path.encode("ascii")

        await self.app(scope, receive, send)


def webhook_token_from_scope(scope: dict[str, Any], route_token: str) -> str:
    state = scope.get("state") or {}
    value = state.get(JUDIT_WEBHOOK_TOKEN_STATE_KEY)
    return str(value) if value else route_token
