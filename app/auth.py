from __future__ import annotations

import hmac
import json
import os
from uuid import UUID

from fastapi import HTTPException, Request


def configured_bearer_tokens() -> dict[str, UUID]:
    raw = os.environ.get("RPY_BEARER_TOKENS", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("RPY_BEARER_TOKENS must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("RPY_BEARER_TOKENS must be a JSON object mapping token to tenant UUID")

    configured: dict[str, UUID] = {}
    for token, tenant_id in parsed.items():
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError("RPY_BEARER_TOKENS contains an empty bearer token")
        if not isinstance(tenant_id, str):
            raise RuntimeError("RPY_BEARER_TOKENS tenant ids must be UUID strings")
        try:
            configured[token] = UUID(tenant_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise RuntimeError(
                f"RPY_BEARER_TOKENS contains an invalid tenant UUID for one configured token"
            ) from exc
    return configured


def tenant_from_request(request: Request) -> UUID:
    authorization = request.headers.get("authorization", "")
    scheme, _, supplied = authorization.partition(" ")
    supplied = supplied.strip()
    if scheme.casefold() != "bearer" or not supplied:
        raise HTTPException(status_code=401, detail="invalid bearer token")

    configured = getattr(request.app.state, "bearer_tokens", None)
    if configured is None:
        # Tests and embedded ASGI usage may bypass lifespan. Production parses and
        # validates once at startup and stores the immutable mapping on app.state.
        configured = configured_bearer_tokens()

    for token, tenant_id in configured.items():
        if hmac.compare_digest(supplied, token):
            return tenant_id
    raise HTTPException(status_code=401, detail="invalid bearer token")
