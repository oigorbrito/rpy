from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from app.db import create_pool
from app.judit_client import JuditRequestError, create_lawsuit_request
from app.queue import enqueue
from app.tasks import PermanentTaskError, task


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
    """Durably register a tenant/CNJ acquisition without provider I/O.

    The tenant does not gain access merely because the CNJ already exists for
    another tenant. Provider work is delegated to the PostgreSQL job queue.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT id, judit_request_id, status FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2 FOR UPDATE",
                tenant_id,
                code,
            )
            if existing is not None:
                return ProcessRequestResult(
                    request_id=str(existing["judit_request_id"] or ""), created=False
                )

            row = await conn.fetchrow(
                """
                INSERT INTO tenant_judit_requests (tenant_id, process_code, status)
                VALUES ($1, $2, 'processing')
                RETURNING id
                """,
                tenant_id,
                code,
            )
            request_row_id = row["id"]
            await enqueue(
                conn,
                task_name="request_judit_process",
                payload={"tenant_request_id": str(request_row_id)},
                max_attempts=1,
                idempotency_key=f"judit-acquire:{request_row_id}",
            )
            return ProcessRequestResult(request_id="", created=True)


async def _reconcile_tenant_access(
    conn: asyncpg.Connection, *, request_id: str, tenant_id: UUID
) -> None:
    process_id = await conn.fetchval(
        """
        SELECT process_id
        FROM process_versions
        WHERE source_request_id = $1
        ORDER BY source_received_at DESC
        LIMIT 1
        """,
        request_id,
    )
    if process_id is not None:
        await conn.execute(
            "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            tenant_id,
            process_id,
        )


@task("request_judit_process")
async def request_judit_process_task(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        tenant_request_id = UUID(str(payload["tenant_request_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise PermanentTaskError("invalid tenant process request payload") from exc

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    pool = await create_pool(database_url, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            request_row = await conn.fetchrow(
                "SELECT tenant_id, process_code, judit_request_id, status FROM tenant_judit_requests WHERE id=$1",
                tenant_request_id,
            )
        if request_row is None:
            raise PermanentTaskError("tenant process request not found")
        if request_row["judit_request_id"]:
            return {"status": str(request_row["status"])}

        try:
            provider = await create_lawsuit_request(str(request_row["process_code"]))
        except JuditRequestError as exc:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE tenant_judit_requests SET status='failed' WHERE id=$1 AND judit_request_id IS NULL",
                    tenant_request_id,
                )
            # Provider acceptance is unknowable after an ambiguous transport failure;
            # do not retry automatically and risk duplicate paid requests.
            raise PermanentTaskError("process provider request failed") from exc

        async with pool.acquire() as conn:
            async with conn.transaction():
                updated = await conn.fetchrow(
                    """
                    UPDATE tenant_judit_requests
                    SET judit_request_id=$2, status='processing'
                    WHERE id=$1 AND judit_request_id IS NULL
                    RETURNING tenant_id
                    """,
                    tenant_request_id,
                    provider.request_id,
                )
                if updated is not None:
                    await _reconcile_tenant_access(
                        conn, request_id=provider.request_id, tenant_id=updated["tenant_id"]
                    )
        return {"status": "processing"}
    finally:
        await pool.close()


async def grant_request_tenants(
    conn: asyncpg.Connection, *, request_id: str, process_id: UUID
) -> None:
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
