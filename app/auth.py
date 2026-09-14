from __future__ import annotations

import hmac
import json
import os
from uuid import UUID

from fastapi import HTTPException, Request


def _configured_tokens() -> dict[str, str]:
    raw = os.environ.get("RPY_BEARER_TOKENS", "{}")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("RPY_BEARER_TOKENS must be a JSON object mapping token to tenant UUID")
    return {str(token): str(tenant_id) for token, tenant_id in parsed.items()}


def tenant_from_request(request: Request) -> UUID:
    authorization = request.headers.get("authorization", "")
    scheme, _, supplied = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not supplied:
        raise HTTPException(status_code=401, detail="invalid bearer token")

    for configured, tenant_id in _configured_tokens().items():
        if hmac.compare_digest(supplied, configured):
            return UUID(tenant_id)
    raise HTTPException(status_code=401, detail="invalid bearer token")
