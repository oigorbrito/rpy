from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api_key_auth import (
    RequestPrincipal,
    apply_rate_limit_headers,
    authorize_api_key_process,
    log_principal_access,
)
from app.attachment_signals import attachment_status_flags
from app.auth import principal_from_request, principal_from_request_unscoped, tenant_from_request
from app.judit import normalize_cnj
from app.json_utils import decode_json_object
from app.process_requests import request_process
from app.processes import get_authorized_process, log_access
from app.provenance import load_used_summary_sources
from app.public_lifecycle import (
    IdempotencyConflictError,
    create_or_get_summary_request,
    request_fingerprint,
)

router = APIRouter()


def _json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    return decode_json_object(value, label="public API JSON object")


def _response(payload: dict[str, Any], *, principal: RequestPrincipal, status_code: int = 200):
    response = JSONResponse(status_code=status_code, content=jsonable_encoder(payload))
    apply_rate_limit_headers(response, principal)
    return response


def _summary_usage(row: asyncpg.Record) -> dict[str, Any] | None:
    if row["model"] is None:
        return None
    result: dict[str, Any] = {
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "generation_ms": int(row["generation_ms"] or 0),
    }
    provider_usage = _json_object(row["provider_usage"])
    result.update(provider_usage)
    if row["cache_hit"] is not None:
        result["cache_hit"] = bool(row["cache_hit"])
    if row["cost_usd"] is not None:
        result["cost_usd"] = float(row["cost_usd"])
    return result


async def _load_attachment_flags(
    conn: asyncpg.Connection,
    *,
    process_id: UUID | None,
    version_id: UUID | None,
) -> dict[str, Any]:
    if process_id is None or version_id is None:
        return {}
    row = await conn.fetchrow(
        """
        SELECT pending_count, ready_count, unavailable_count, corrupt_count, unreadable_count
        FROM process_attachment_status_counts
        WHERE process_id=$1 AND version_id=$2
        """,
        process_id,
        version_id,
    )
    if row is None:
        return {}
    return attachment_status_flags(
        {
            "pending": row["pending_count"],
            "ready": row["ready_count"],
            "unavailable": row["unavailable_count"],
            "corrupt": row["corrupt_count"],
            "unreadable": row["unreadable_count"],
        }
    )


async def _load_sources(
    conn: asyncpg.Connection,
    *,
    process_id: UUID | None,
    version_id: UUID | None,
    summary_id: UUID | None = None,
) -> list[dict[str, Any]]:
    if process_id is None or version_id is None:
        return []

    version = await conn.fetchrow(
        """
        SELECT pv.id, pv.source_cached_response, pv.finalized_at, p.secrecy_level
        FROM process_versions pv
        JOIN processes p ON p.id = pv.process_id
        WHERE pv.id = $1 AND pv.process_id = $2 AND pv.finalized = TRUE
        """,
        version_id,
        process_id,
    )
    if version is None:
        return []

    root_source: dict[str, Any] = {
        "kind": "judit_lawsuit",
        "source_version": str(version["id"]),
        "cached": bool(version["source_cached_response"]),
        "finalized_at": version["finalized_at"],
    }
    if summary_id is not None:
        root_source["used_for_summary"] = True
    sources: list[dict[str, Any]] = [root_source]

    # Restricted processes never expose movement-level provenance through the
    # public sources endpoint, even if older rows exist from a prior policy.
    if int(version["secrecy_level"] or 0) > 0:
        return sources

    if summary_id is not None:
        used_sources = await load_used_summary_sources(conn, summary_id=summary_id)
        if used_sources:
            sources.extend(used_sources)
            return sources

    # Compatibility fallback for summaries created before provenance persistence
    # and for process/source reads that do not yet have a published summary.
    steps = await conn.fetch(
        """
        SELECT id,
               step_number,
               occurred_at,
               title,
               CASE
                   WHEN jsonb_typeof(metadata->'source_step_number') = 'number'
                   THEN (metadata->>'source_step_number')::bigint
                   ELSE NULL
               END AS source_step_number
        FROM process_steps
        WHERE process_id = $1 AND version_id = $2
        ORDER BY step_number
        """,
        process_id,
        version_id,
    )
    sources.extend(
        {
            "kind": "movement",
            "step_id": str(row["id"]),
            "step_number": int(row["step_number"]),
            "source_step_number": (
                int(row["source_step_number"])
                if row["source_step_number"] is not None
                else None
            ),
            "occurred_at": row["occurred_at"],
            "title": row["title"],
        }
        for row in steps
    )
    return sources


async def _job_payload(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    job_id: UUID,
) -> tuple[asyncpg.Record, dict[str, Any]] | None:
    row = await conn.fetchrow(
        """
        SELECT psr.*,
               ps.markdown,
               ps.validation,
               ps.model,
               ps.prompt_version,
               ps.generation_ms,
               ps.usage AS provider_usage,
               ps.cache_hit,
               ps.cost_usd
        FROM public_summary_requests psr
        LEFT JOIN process_summaries ps ON ps.id = psr.summary_id
        WHERE psr.id = $1 AND psr.tenant_id = $2
        """,
        job_id,
        tenant_id,
    )
    if row is None:
        return None
    sources = await _load_sources(
        conn,
        process_id=row["process_id"],
        version_id=row["version_id"],
        summary_id=row["summary_id"],
    )
    validation = (
        _json_object(row["validation"])
        if row["validation"] is not None
        else None
    )
    flags = _json_object(row["flags"])
    flags.update(
        await _load_attachment_flags(
            conn,
            process_id=row["process_id"],
            version_id=row["version_id"],
        )
    )
    payload = {
        "job_id": str(row["id"]),
        "poll_url": f"/v1/resumos/{row['id']}",
        "status": str(row["status"]),
        "cnj": str(row["process_code"]),
        "source_updated_at": row["source_updated_at"],
        "sources": sources,
        "usage": _summary_usage(row),
        "flags": flags,
        "validation": validation,
        "iaSummary": row["markdown"] if validation and validation.get("passed") is True else None,
        "error_code": row["error_code"],
    }
    return row, payload


async def _latest_summary_payload(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    code: str,
) -> dict[str, Any] | None:
    process = await get_authorized_process(conn, tenant_id=tenant_id, code=code)
    if process is None or process["current_version_id"] is None:
        return None
    summary = await conn.fetchrow(
        """
        SELECT id, markdown, validation, model, prompt_version, generation_ms,
               usage AS provider_usage, cache_hit, cost_usd, created_at
        FROM process_summaries
        WHERE process_id = $1
          AND version_id = $2
          AND COALESCE((validation->>'passed')::boolean, false) = true
        """,
        process["id"],
        process["current_version_id"],
    )
    if summary is None:
        return None
    validation = _json_object(summary["validation"])
    sources = await _load_sources(
        conn,
        process_id=process["id"],
        version_id=process["current_version_id"],
        summary_id=summary["id"],
    )
    flags = {"secrecy": int(process["secrecy_level"] or 0) > 0}
    flags.update(
        await _load_attachment_flags(
            conn,
            process_id=process["id"],
            version_id=process["current_version_id"],
        )
    )
    return {
        "cnj": code,
        "source_updated_at": process["updated_at"],
        "sources": sources,
        "usage": _summary_usage(summary),
        "flags": flags,
        "validation": validation,
        "iaSummary": summary["markdown"],
    }


@router.post("/v1/resumos", status_code=202)
async def create_summary_job(request: Request):
    try:
        body = await request.json()
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="invalid JSON payload") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")
    raw_code = body.get("cnj")
    if not isinstance(raw_code, str):
        raise HTTPException(status_code=400, detail="cnj is required")
    try:
        code = normalize_cnj(raw_code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None

    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    if len(idempotency_key) > 255:
        raise HTTPException(status_code=400, detail="Idempotency-Key is too long")

    principal = await principal_from_request(request, process_code=code)
    normalized_body = dict(body)
    normalized_body["cnj"] = code
    fingerprint = request_fingerprint(normalized_body)
    pool: asyncpg.Pool = request.app.state.pool

    try:
        async with pool.acquire() as conn:
            public_request, created = await create_or_get_summary_request(
                conn,
                tenant_id=principal.tenant_id,
                process_code=code,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
    except IdempotencyConflictError:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with a different request",
        ) from None

    if created or public_request.tenant_judit_request_id is None:
        await request_process(
            pool,
            tenant_id=principal.tenant_id,
            code=code,
            public_summary_request_id=public_request.id,
        )

    async with pool.acquire() as conn:
        loaded = await _job_payload(
            conn,
            tenant_id=principal.tenant_id,
            job_id=public_request.id,
        )
        if loaded is None:
            raise RuntimeError("public summary request disappeared after creation")
        _, payload = loaded
        await log_principal_access(
            conn,
            principal=principal,
            process_id=public_request.process_id,
            process_code=code,
            action="v1_create_summary",
            metadata={"created": created},
        )
    return _response(payload, principal=principal, status_code=202)


@router.get("/v1/resumos/{job_id}")
async def get_summary_job(job_id: str, request: Request):
    try:
        request_id = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="summary job not found") from None

    principal = await principal_from_request_unscoped(request)
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        loaded = await _job_payload(
            conn,
            tenant_id=principal.tenant_id,
            job_id=request_id,
        )
    if loaded is None:
        raise HTTPException(status_code=404, detail="summary job not found")
    row, payload = loaded

    principal = await authorize_api_key_process(
        request,
        principal=principal,
        process_code=str(row["process_code"]),
    )
    async with pool.acquire() as conn:
        await log_principal_access(
            conn,
            principal=principal,
            process_id=row["process_id"],
            process_code=str(row["process_code"]),
            action="v1_read_summary_job",
        )
    return _response(payload, principal=principal)


@router.get("/v1/processos/{code}/resumo")
async def get_latest_summary(code: str, request: Request):
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None
    tenant_id = tenant_from_request(request)
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        payload = await _latest_summary_payload(
            conn,
            tenant_id=tenant_id,
            code=canonical_code,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="summary not found")
        state = getattr(request, "state", None)
        principal = getattr(state, "principal", None)
        if not isinstance(principal, RequestPrincipal):
            await log_access(
                conn,
                tenant_id=tenant_id,
                process_id=None,
                process_code=canonical_code,
                action="v1_read_summary",
            )
    return payload


@router.get("/v1/processos/{code}/fontes")
async def get_summary_sources(code: str, request: Request):
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None
    tenant_id = tenant_from_request(request)
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        process = await get_authorized_process(conn, tenant_id=tenant_id, code=canonical_code)
        if process is None:
            raise HTTPException(status_code=404, detail="process not found")
        summary_id = None
        if process["current_version_id"] is not None:
            summary_id = await conn.fetchval(
                """
                SELECT id
                FROM process_summaries
                WHERE process_id = $1
                  AND version_id = $2
                  AND COALESCE((validation->>'passed')::boolean, false) = true
                """,
                process["id"],
                process["current_version_id"],
            )
        sources = await _load_sources(
            conn,
            process_id=process["id"],
            version_id=process["current_version_id"],
            summary_id=summary_id,
        )
        state = getattr(request, "state", None)
        principal = getattr(state, "principal", None)
        if not isinstance(principal, RequestPrincipal):
            await log_access(
                conn,
                tenant_id=tenant_id,
                process_id=process["id"],
                process_code=canonical_code,
                action="v1_read_sources",
            )
        flags = {"secrecy": int(process["secrecy_level"] or 0) > 0}
        flags.update(
            await _load_attachment_flags(
                conn,
                process_id=process["id"],
                version_id=process["current_version_id"],
            )
        )
        return {
            "cnj": canonical_code,
            "source_updated_at": process["updated_at"],
            "sources": sources,
            "flags": flags,
        }
