from __future__ import annotations

import asyncio
import hmac
import json
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException, Request

from app.auth import configured_bearer_tokens, tenant_from_request
from app.db import create_pool
from app.http_limits import JuditWebhookBodyLimitMiddleware, judit_webhook_max_body_bytes
from app.json_utils import decode_json_object
from app.judit import parse_event
from app.observability import collect_operational_metrics
from app.processes import get_authorized_process, log_access, stage_version
from app.queue import enqueue


@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    # Fail deployment startup on invalid security/runtime configuration instead of
    # discovering it only after the first production request arrives.
    judit_webhook_max_body_bytes()
    app.state.bearer_tokens = configured_bearer_tokens()
    app.state.pool = await create_pool(database_url)
    try:
        yield
    finally:
        await app.state.pool.close()


app = FastAPI(title="Rpy", lifespan=lifespan)
app.add_middleware(JuditWebhookBodyLimitMiddleware)


def _valid_webhook_token(token: str) -> bool:
    expected = os.environ.get("JUDIT_WEBHOOK_TOKEN", "")
    return bool(expected) and hmac.compare_digest(token, expected)


def _valid_ops_request(request: Request) -> bool:
    expected = os.environ.get("RPY_OPS_TOKEN", "")
    authorization = request.headers.get("authorization", "")
    if not expected or not authorization.startswith("Bearer "):
        return False
    supplied = authorization.removeprefix("Bearer ").strip()
    return bool(supplied) and hmac.compare_digest(supplied, expected)


@app.get("/health")
async def health() -> dict[str, bool]:
    """Process liveness probe; deliberately does not depend on PostgreSQL."""
    return {"ok": True}


@app.get("/ready")
async def ready(request: Request) -> dict[str, bool]:
    """Readiness probe: traffic is accepted only while PostgreSQL is reachable."""
    pool: asyncpg.Pool = request.app.state.pool

    async def _probe() -> None:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    try:
        await asyncio.wait_for(_probe(), timeout=2.0)
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
        raise HTTPException(status_code=503, detail="database unavailable") from None
    return {"ok": True}


@app.get("/ops/metrics")
async def operational_metrics(request: Request) -> dict:
    # Hide the existence of the operational surface when the token is absent/invalid.
    if not _valid_ops_request(request):
        raise HTTPException(status_code=404, detail="not found")
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        return await collect_operational_metrics(conn)


@app.get("/processes/{code}")
async def get_process_summary(code: str, request: Request) -> dict:
    tenant_id = tenant_from_request(request)
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        process = await get_authorized_process(conn, tenant_id=tenant_id, code=code)
        if process is None:
            raise HTTPException(status_code=404, detail="process not found")
        await log_access(
            conn,
            tenant_id=tenant_id,
            process_id=process["id"],
            process_code=code,
            action="read_process_summary",
        )
        summary = await conn.fetchrow(
            """
            SELECT markdown, validation, model, prompt_version, generation_ms, created_at
            FROM process_summaries
            WHERE process_id = $1 AND version_id = $2
            """,
            process["id"],
            process["current_version_id"],
        )

    summary_data = dict(summary) if summary else None
    if summary_data is not None:
        summary_data["validation"] = decode_json_object(
            summary_data.get("validation"), label="summary validation"
        )

    return {
        "code": process["code"],
        "class_name": process["class_name"],
        "court": process["court"],
        "summary": summary_data,
    }


async def _record_delivery(conn: asyncpg.Connection, event) -> bool:
    """Return False when this callback_id has already been persisted."""
    if not event.callback_id:
        return True
    inserted = await conn.fetchval(
        """
        INSERT INTO judit_deliveries (callback_id, request_id, event_type, raw_payload)
        VALUES ($1, $2, $3, $4::jsonb)
        ON CONFLICT (callback_id) DO NOTHING
        RETURNING callback_id
        """,
        event.callback_id,
        event.request_id,
        event.event_type,
        json.dumps(event.raw),
    )
    return inserted is not None


@app.post("/webhooks/judit/{token}")
async def judit_webhook(token: str, request: Request) -> dict[str, bool]:
    if not _valid_webhook_token(token):
        raise HTTPException(status_code=404, detail="not found")

    try:
        body = await request.json()
        event = parse_event(body)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="invalid payload") from None

    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        # Delivery dedupe and its corresponding durable side effect are one unit.
        # If staging/enqueue fails, the delivery row rolls back so Judit can retry
        # the same callback_id without the event being discarded as a duplicate.
        async with conn.transaction():
            if not await _record_delivery(conn, event):
                return {"ok": True}

            if event.is_lawsuit_response:
                source_id = event.response_id or event.callback_id
                await stage_version(
                    conn,
                    code=str(event.code),
                    source_request_id=source_id,
                    cached_response=event.cached_response,
                    payload=event.raw,
                    judit_request_id=event.request_id,
                    judit_response_id=event.response_id,
                    judit_callback_id=event.callback_id,
                )

            elif event.request_completed and event.request_id:
                await enqueue(
                    conn,
                    task_name="finalize_judit_request",
                    payload={"request_id": event.request_id},
                    idempotency_key=f"judit-finalize:{event.request_id}",
                )

    return {"ok": True}
