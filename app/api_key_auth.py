from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import HTTPException, Request, Response


@dataclass(frozen=True)
class RequestPrincipal:
    tenant_id: UUID
    api_key_id: UUID | None = None
    api_key_fingerprint: str | None = None
    rate_limit_remaining: int | None = None

    @property
    def uses_api_key(self) -> bool:
        return self.api_key_id is not None


def api_key_hash_and_fingerprint(token: str) -> tuple[str, str]:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return digest, digest[:16]


def api_key_environment(token: str) -> str | None:
    if token.startswith("sk_live_"):
        return "live"
    if token.startswith("sk_test_"):
        return "test"
    return None


def bearer_credential(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, supplied = authorization.partition(" ")
    supplied = supplied.strip()
    if scheme.casefold() != "bearer" or not supplied:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return supplied


async def _consume_rate_limit(
    conn: asyncpg.Connection,
    *,
    api_key_id: UUID,
    limit: int,
) -> tuple[int, int]:
    """Consume one fixed-window request and return (remaining, retry_after_seconds)."""
    await conn.execute(
        """
        INSERT INTO api_key_rate_limits (api_key_id)
        VALUES ($1)
        ON CONFLICT (api_key_id) DO NOTHING
        """,
        api_key_id,
    )
    row = await conn.fetchrow(
        """
        SELECT window_started_at, request_count
        FROM api_key_rate_limits
        WHERE api_key_id = $1
        FOR UPDATE
        """,
        api_key_id,
    )
    if row is None:
        raise RuntimeError("API key rate-limit row was not created")

    now = datetime.now(timezone.utc)
    window_started_at = row["window_started_at"]
    elapsed = (now - window_started_at).total_seconds()
    if elapsed >= 60:
        request_count = 1
        await conn.execute(
            """
            UPDATE api_key_rate_limits
            SET window_started_at = date_trunc('minute', NOW()),
                request_count = 1,
                updated_at = NOW()
            WHERE api_key_id = $1
            """,
            api_key_id,
        )
        return max(limit - request_count, 0), 0

    request_count = int(row["request_count"])
    retry_after = max(1, int(60 - elapsed))
    if request_count >= limit:
        return 0, retry_after

    request_count += 1
    await conn.execute(
        """
        UPDATE api_key_rate_limits
        SET request_count = $2,
            updated_at = NOW()
        WHERE api_key_id = $1
        """,
        api_key_id,
        request_count,
    )
    return max(limit - request_count, 0), 0


async def authenticate_api_key(
    request: Request,
    *,
    token: str,
    process_code: str,
) -> RequestPrincipal:
    environment = api_key_environment(token)
    if environment is None:
        raise HTTPException(status_code=401, detail="invalid API key")

    key_hash, expected_fingerprint = api_key_hash_and_fingerprint(token)
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            key = await conn.fetchrow(
                """
                SELECT id, tenant_id, key_hash, fingerprint, environment,
                       allow_portfolio, rate_limit_per_minute,
                       expires_at, revoked_at
                FROM api_keys
                WHERE key_hash = $1
                FOR UPDATE
                """,
                key_hash,
            )
            if key is None:
                raise HTTPException(status_code=401, detail="invalid API key")
            if not hmac.compare_digest(str(key["key_hash"]), key_hash):
                raise HTTPException(status_code=401, detail="invalid API key")
            if not hmac.compare_digest(str(key["fingerprint"]), expected_fingerprint):
                raise HTTPException(status_code=401, detail="invalid API key")
            if str(key["environment"]) != environment:
                raise HTTPException(status_code=401, detail="invalid API key")
            if key["revoked_at"] is not None:
                raise HTTPException(status_code=401, detail="revoked API key")
            if key["expires_at"] is not None and key["expires_at"] <= datetime.now(timezone.utc):
                raise HTTPException(status_code=401, detail="expired API key")

            remaining, retry_after = await _consume_rate_limit(
                conn,
                api_key_id=key["id"],
                limit=int(key["rate_limit_per_minute"]),
            )
            if retry_after:
                raise HTTPException(
                    status_code=429,
                    detail="rate limit exceeded",
                    headers={
                        "X-RateLimit-Remaining": "0",
                        "Retry-After": str(retry_after),
                    },
                )

            explicit_scope = bool(
                await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM api_key_cnj_scopes
                        WHERE api_key_id = $1 AND process_code = $2
                    )
                    """,
                    key["id"],
                    process_code,
                )
            )
            portfolio_scope = False
            if bool(key["allow_portfolio"]):
                portfolio_scope = bool(
                    await conn.fetchval(
                        """
                        SELECT EXISTS(
                            SELECT 1
                            FROM tenant_processes tp
                            JOIN processes p ON p.id = tp.process_id
                            WHERE tp.tenant_id = $1 AND p.code = $2
                        )
                        """,
                        key["tenant_id"],
                        process_code,
                    )
                )

            if not explicit_scope and not portfolio_scope:
                await conn.execute(
                    """
                    INSERT INTO access_log (
                        tenant_id, process_id, process_code, action, metadata,
                        api_key_id, api_key_fingerprint
                    )
                    VALUES ($1, NULL, $2, 'authorization_denied', '{}'::jsonb, $3, $4)
                    """,
                    key["tenant_id"],
                    process_code,
                    key["id"],
                    key["fingerprint"],
                )
                raise HTTPException(status_code=404, detail="process not found")

            await conn.execute(
                "UPDATE api_keys SET last_used_at = NOW() WHERE id = $1",
                key["id"],
            )
            return RequestPrincipal(
                tenant_id=key["tenant_id"],
                api_key_id=key["id"],
                api_key_fingerprint=str(key["fingerprint"]),
                rate_limit_remaining=remaining,
            )


async def log_principal_access(
    conn: asyncpg.Connection,
    *,
    principal: RequestPrincipal,
    process_id: UUID | None,
    process_code: str,
    action: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO access_log (
            tenant_id, process_id, process_code, action, metadata,
            api_key_id, api_key_fingerprint
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
        """,
        principal.tenant_id,
        process_id,
        process_code,
        action,
        json.dumps(metadata or {}),
        principal.api_key_id,
        principal.api_key_fingerprint,
    )


def apply_rate_limit_headers(response: Response, principal: RequestPrincipal) -> None:
    if principal.rate_limit_remaining is not None:
        response.headers["X-RateLimit-Remaining"] = str(principal.rate_limit_remaining)
