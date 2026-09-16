from __future__ import annotations

import hmac
import json
import os
from uuid import UUID

from fastapi import HTTPException, Request

from app.api_key_auth import (
    RequestPrincipal,
    api_key_environment,
    authenticate_api_key,
    bearer_credential,
)


def configured_bearer_tokens() -> dict[str, UUID]:
    raw = os.environ.get("RPY_BEARER_TOKENS", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("RPY_BEARER_TOKENS must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("RPY_BEARER_TOKENS must be a JSON object mapping token to tenant UUID")
    if not parsed:
        raise RuntimeError("RPY_BEARER_TOKENS must configure at least one bearer token")

    configured: dict[str, UUID] = {}
    for token, tenant_id in parsed.items():
        if not isinstance(token, str) or not token:
            raise RuntimeError("RPY_BEARER_TOKENS contains an empty bearer token")
        # HTTP Authorization credentials cannot reliably preserve whitespace as part
        # of the token. tenant_from_request intentionally strips transport spacing,
        # so accepting whitespace here would create a startup-successful credential
        # that can never match (or whose meaning depends on intermediaries).
        if token != token.strip() or any(char.isspace() for char in token):
            raise RuntimeError(
                "RPY_BEARER_TOKENS bearer tokens must not contain whitespace"
            )
        if not isinstance(tenant_id, str):
            raise RuntimeError("RPY_BEARER_TOKENS tenant ids must be UUID strings")
        try:
            configured[token] = UUID(tenant_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise RuntimeError(
                "RPY_BEARER_TOKENS contains an invalid tenant UUID for one configured token"
            ) from exc
    return configured


def _legacy_tenant_for_token(request: Request, supplied: str) -> UUID:
    configured = getattr(request.app.state, "bearer_tokens", None)
    if configured is None:
        # Tests and embedded ASGI usage may bypass lifespan. Production parses and
        # validates once at startup and stores the immutable mapping on app.state.
        configured = configured_bearer_tokens()

    for token, tenant_id in configured.items():
        if hmac.compare_digest(supplied, token):
            return tenant_id
    raise HTTPException(status_code=401, detail="invalid bearer token")


def tenant_from_request(request: Request) -> UUID:
    """Legacy bearer authentication retained for compatibility during migration."""
    supplied = bearer_credential(request)
    if api_key_environment(supplied) is not None:
        raise HTTPException(status_code=401, detail="API key requires scoped authentication")
    return _legacy_tenant_for_token(request, supplied)


async def principal_from_request(
    request: Request,
    *,
    process_code: str,
) -> RequestPrincipal:
    supplied = bearer_credential(request)
    if api_key_environment(supplied) is not None:
        return await authenticate_api_key(
            request,
            token=supplied,
            process_code=process_code,
        )
    return RequestPrincipal(tenant_id=_legacy_tenant_for_token(request, supplied))
