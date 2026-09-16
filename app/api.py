from __future__ import annotations

import asyncio
import hmac
import json
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException, Request

from app.api_key_middleware import ApiKeySecurityMiddleware
from app.api_v1 import router as api_v1_router
from app.auth import configured_bearer_tokens, tenant_from_request
from app.db import create_pool
from app.frontend import router as frontend_router
from app.http_auth_config import validate_http_auth_config
from app.http_limits import JuditWebhookBodyLimitMiddleware, judit_webhook_max_body_bytes
from app.json_utils import decode_json_object
from app.judit import normalize_cnj, parse_event
from app.judit_client import JuditRequestError
from app.observability import (
    collect_operational_metrics,
    list_failed_summaries,
    operational_thresholds,
)
from app.process_requests import grant_request_tenants, request_process
from app.processes import get_authorized_process, log_access, stage_version
from app.queue import enqueue
from app.tenancy import configured_webhook_tenant, validate_carteira_seed
from app.webhook_security import (
    JuditWebhookSecretRedactionMiddleware,
    webhook_token_from_scope,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    judit_webhook_max_body_bytes()
    operational_thresholds()
    validate_http_auth_config()
    app.state.bearer_tokens = configured_bearer_tokens()
    app.state.webhook_tenant_id = configured_webhook_tenant()
    validate_carteira_seed()
    app.state.pool = await create_pool(database_url)
    try:
        yield
    finally:
        await app.state.pool.close()


app = FastAPI(title="Rpy", lifespan=lifespan)
app.add_middleware(ApiKeySecurityMiddleware)
app.add_middleware(JuditWebhookBodyLimitMiddleware)
app.add_middleware(JuditWebhookSecretRedactionMiddleware)
app.include_router(api_v1_router)
app.include_router(frontend_router)


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


def _json_value(value, *, fallback):
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


def _summary_status(summary, job_status: str | None, cached_response: bool) -> str:
    if summary is not None:
        return "available"
    if cached_response:
        return "not_generated"
    if job_status in {"pending", "processing"}:
        return "processing"
    if job_status == "dead":
        return "unavailable"
    return "not_generated"


@app.get("/health")
@app.get("/healthz")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/ready")
@app.get("/readyz")
async def ready(request: Request) -> dict[str, bool]:
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
    if not _valid_ops_request(request):
        raise HTTPException(status_code=404, detail="not found")
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        return await collect_operational_metrics(conn)


@app.get("/ops/failed-summaries")
async def failed_summaries(request: Request) -> dict:
    if not _valid_ops_request(request):
        raise HTTPException(status_code=404, detail="not found")
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        return {"failed_summaries": await list_failed_summaries(conn)}


@app.post("/processes/{code}/request", status_code=202)
async def create_process_request(code: str, request: Request) -> dict:
    tenant_id = tenant_from_request(request)
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None
    try:
        result = await request_process(
            request.app.state.pool, tenant_id=tenant_id, code=canonical_code
        )
    except JuditRequestError:
        raise HTTPException(status_code=503, detail="process provider unavailable") from None
    async with request.app.state.pool.acquire() as conn:
        process = await get_authorized_process(conn, tenant_id=tenant_id, code=canonical_code)
        await log_access(
            conn,
            tenant_id=tenant_id,
            process_id=process["id"] if process else None,
            process_code=canonical_code,
            action="request_process",
            metadata={"created": result.created},
        )
    return {
        "code": canonical_code,
        "status": "available" if process else "processing",
        "created": result.created,
    }


@app.get("/processes/{code}")
async def get_process_summary(code: str, request: Request) -> dict:
    tenant_id = tenant_from_request(request)
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        process = await get_authorized_process(conn, tenant_id=tenant_id, code=canonical_code)
        if process is None:
            raise HTTPException(status_code=404, detail="process not found")
        await log_access(
            conn,
            tenant_id=tenant_id,
            process_id=process["id"],
            process_code=canonical_code,
            action="read_process_summary",
        )
        summary = await conn.fetchrow(
            """
            SELECT markdown, validation, model, prompt_version, generation_ms, created_at
            FROM process_summaries
            WHERE process_id = $1
              AND version_id = $2
              AND COALESCE((validation->>'passed')::boolean, false) = true
            """,
            process["id"],
            process["current_version_id"],
        )
        steps = []
        summary_job_status = None
        cached_response = False
        if process["current_version_id"] is not None:
            steps = await conn.fetch(
                """
                SELECT step_number, occurred_at, title, text
                FROM process_steps
                WHERE process_id = $1 AND version_id = $2
                ORDER BY occurred_at DESC NULLS LAST, step_number DESC
                LIMIT 20
                """,
                process["id"],
                process["current_version_id"],
            )
            cached_response = bool(
                await conn.fetchval(
                    "SELECT source_cached_response FROM process_versions WHERE id = $1 AND process_id = $2",
                    process["current_version_id"],
                    process["id"],
                )
            )
            summary_job_status = await conn.fetchval(
                """
                SELECT status::text FROM jobs
                WHERE task_name = 'generate_process_summary' AND idempotency_key = $1
                ORDER BY created_at DESC LIMIT 1
                """,
                f"summary:{process['current_version_id']}",
            )

    summary_data = dict(summary) if summary else None
    if summary_data is not None:
        summary_data["validation"] = decode_json_object(
            summary_data.get("validation"), label="summary validation"
        )

    ia_summary = summary_data["markdown"] if summary_data else None
    parties = _json_value(process["parties"], fallback=[])
    subjects = _json_value(process["subjects"], fallback=[])
    header = _json_value(process["header"], fallback={})

    return {
        "code": process["code"],
        "class_name": process["class_name"],
        "court": process["court"],
        "parties": parties if isinstance(parties, list) else [],
        "subjects": subjects if isinstance(subjects, list) else [],
        "header": header if isinstance(header, dict) else {},
        "updated_at": process["updated_at"],
        "summary_status": _summary_status(summary_data, summary_job_status, cached_response),
        "summary": summary_data,
        "iaSummary": ia_summary,
        "recent_steps": [dict(step) for step in steps],
    }


async def _record_delivery(conn: asyncpg.Connection, event) -> bool:
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


async def _record_request_completion(conn: asyncpg.Connection, request_id: str) -> None:
    await conn.execute(
        "INSERT INTO judit_request_completions (request_id) VALUES ($1) ON CONFLICT (request_id) DO NOTHING",
        request_id,
    )
    await conn.execute(
        "UPDATE tenant_judit_requests SET completed_at = COALESCE(completed_at, NOW()) WHERE judit_request_id = $1",
        request_id,
    )


async def _request_was_completed(conn: asyncpg.Connection, request_id: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM judit_request_completions WHERE request_id = $1)",
            request_id,
        )
    )


async def _enqueue_finalize(
    conn: asyncpg.Connection, *, request_id: str, idempotency_key: str
) -> None:
    await enqueue(
        conn,
        task_name="finalize_judit_request",
        payload={"request_id": request_id},
        idempotency_key=idempotency_key,
    )


@app.post("/webhooks/judit/{token}")
async def judit_webhook(token: str, request: Request) -> dict[str, bool]:
    supplied_token = webhook_token_from_scope(request.scope, token)
    if not _valid_webhook_token(supplied_token):
        raise HTTPException(status_code=404, detail="not found")

    try:
        body = await request.json()
        event = parse_event(body)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="invalid payload") from None

    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await _record_delivery(conn, event):
                return {"ok": True}

            if event.is_lawsuit_response:
                source_id = event.response_id or event.callback_id
                process_id, _ = await stage_version(
                    conn,
                    code=str(event.code),
                    source_request_id=source_id,
                    cached_response=event.cached_response,
                    payload=event.raw,
                    judit_request_id=event.request_id,
                    judit_response_id=event.response_id,
                    judit_callback_id=event.callback_id,
                    tenant_id=getattr(request.app.state, "webhook_tenant_id", None),
                )
                if event.request_id:
                    await grant_request_tenants(
                        conn, request_id=str(event.request_id), process_id=process_id
                    )
                if await _request_was_completed(conn, str(event.request_id)):
                    await _enqueue_finalize(
                        conn,
                        request_id=str(event.request_id),
                        idempotency_key=f"judit-finalize-repair:{event.request_id}:{source_id}",
                    )
            elif event.request_completed and event.request_id:
                await _record_request_completion(conn, event.request_id)
                await _enqueue_finalize(
                    conn,
                    request_id=event.request_id,
                    idempotency_key=f"judit-finalize:{event.request_id}",
                )
    return {"ok": True}
