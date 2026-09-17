from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import tenant_from_request
from app.judit import normalize_cnj
from app.judit_tracking import create_tracking, delete_tracking, get_tracking, list_trackings
from app.processes import get_authorized_process, log_access

router = APIRouter(prefix="/v1/trackings", tags=["trackings"])


class TrackingBatchRequest(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=100)
    recurrence_days: int = Field(default=1, ge=1, le=365)


def _tracking_payload(row: asyncpg.Record) -> dict:
    return {
        "tracking_id": str(row["id"]),
        "code": str(row["process_code"]),
        "status": str(row["status"]),
        "recurrence_days": int(row["recurrence_days"]),
        "last_event_at": row["last_event_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def _audit_tracking_action(
    request: Request,
    *,
    tenant_id: UUID,
    code: str,
    action: str,
    metadata: dict | None = None,
) -> None:
    async with request.app.state.pool.acquire() as conn:
        process = await get_authorized_process(conn, tenant_id=tenant_id, code=code)
        await log_access(
            conn,
            tenant_id=tenant_id,
            process_id=process["id"] if process else None,
            process_code=code,
            action=action,
            metadata=metadata,
        )


@router.post("/{code}", status_code=202)
async def create_process_tracking(
    code: str,
    request: Request,
    recurrence_days: int = 1,
) -> dict:
    tenant_id = tenant_from_request(request)
    if recurrence_days < 1 or recurrence_days > 365:
        raise HTTPException(status_code=422, detail="recurrence_days must be between 1 and 365")
    try:
        canonical_code = normalize_cnj(code)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid process code") from None
    result = await create_tracking(
        request.app.state.pool,
        tenant_id=tenant_id,
        code=canonical_code,
        recurrence_days=recurrence_days,
    )
    await _audit_tracking_action(
        request,
        tenant_id=tenant_id,
        code=canonical_code,
        action="create_process_tracking",
        metadata={"created": result.created, "recurrence_days": recurrence_days},
    )
    return {
        "tracking_id": str(result.tracking_id),
        "code": canonical_code,
        "status": result.status,
        "created": result.created,
    }


@router.post("", status_code=202)
async def create_tracking_batch(payload: TrackingBatchRequest, request: Request) -> dict:
    tenant_id = tenant_from_request(request)
    canonical_codes: list[str] = []
    seen: set[str] = set()
    for code in payload.codes:
        try:
            canonical = normalize_cnj(code)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid process code") from None
        if canonical not in seen:
            seen.add(canonical)
            canonical_codes.append(canonical)

    results = []
    for code in canonical_codes:
        result = await create_tracking(
            request.app.state.pool,
            tenant_id=tenant_id,
            code=code,
            recurrence_days=payload.recurrence_days,
        )
        await _audit_tracking_action(
            request,
            tenant_id=tenant_id,
            code=code,
            action="create_process_tracking",
            metadata={"created": result.created, "batch": True},
        )
        results.append(
            {
                "tracking_id": str(result.tracking_id),
                "code": code,
                "status": result.status,
                "created": result.created,
            }
        )
    return {"trackings": results}


@router.get("")
async def list_process_trackings(request: Request) -> dict:
    tenant_id = tenant_from_request(request)
    async with request.app.state.pool.acquire() as conn:
        rows = await list_trackings(conn, tenant_id=tenant_id)
        await log_access(
            conn,
            tenant_id=tenant_id,
            process_id=None,
            process_code="*",
            action="list_process_trackings",
            metadata={"count": len(rows)},
        )
    return {"trackings": [_tracking_payload(row) for row in rows]}


@router.delete("/{tracking_id}", status_code=202)
async def remove_process_tracking(tracking_id: UUID, request: Request) -> dict:
    tenant_id = tenant_from_request(request)
    async with request.app.state.pool.acquire() as conn:
        row = await get_tracking(conn, tenant_id=tenant_id, tracking_id=tracking_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tracking not found")
    deleted = await delete_tracking(
        request.app.state.pool,
        tenant_id=tenant_id,
        tracking_id=tracking_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="tracking not found")
    await _audit_tracking_action(
        request,
        tenant_id=tenant_id,
        code=str(row["process_code"]),
        action="delete_process_tracking",
    )
    return {"tracking_id": str(tracking_id), "status": "deleting"}
