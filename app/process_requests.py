from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.judit_client import JuditRequestError, create_lawsuit_request


@dataclass(frozen=True, slots=True)
class ProcessRequestResult:
    request_id: str
    created: bool


async def request_process(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    code: str,
) -> ProcessRequestResult:
    """Create at most one Judit request for a tenant/CNJ.

    A PostgreSQL advisory transaction lock serializes concurrent callers for the
    same tenant/CNJ without holding a database transaction across provider I/O.
    The durable row is written only after Judit accepts the request.
    """
    lock_key = f"tenant-process-request:{tenant_id}:{code}"
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", lock_key)
        try:
            existing = await conn.fetchrow(
                "SELECT judit_request_id FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",
                tenant_id,
                code,
            )
            if existing is not None:
                return ProcessRequestResult(request_id=existing["judit_request_id"], created=False)

            process = await conn.fetchrow("SELECT id FROM processes WHERE code=$1", code)
            if process is not None:
                await conn.execute(
                    "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                    tenant_id,
                    process["id"],
                )
                return ProcessRequestResult(request_id="", created=False)

            provider = await create_lawsuit_request(code)
            await conn.execute(
                "INSERT INTO tenant_judit_requests (tenant_id, process_code, judit_request_id) VALUES ($1,$2,$3)",
                tenant_id,
                code,
                provider.request_id,
            )
            return ProcessRequestResult(request_id=provider.request_id, created=True)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", lock_key)


async def grant_request_tenants(conn: asyncpg.Connection, *, request_id: str, process_id: UUID) -> None:
    await conn.execute(
        """
        INSERT INTO tenant_processes (tenant_id, process_id)
        SELECT tenant_id, $2
        FROM tenant_judit_requests
        WHERE judit_request_id = $1
        ON CONFLICT DO NOTHING
        """,
        request_id,
        process_id,
    )
