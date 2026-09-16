from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

PROGRESS_STATUSES = (
    "queued",
    "fetching",
    "indexing",
    "generating",
    "validating",
    "completed",
)
TERMINAL_STATUSES = {
    "completed",
    "failed",
    "source_unavailable",
    "secrecy_blocked",
    "validation_failed",
}
ALL_STATUSES = set(PROGRESS_STATUSES) | TERMINAL_STATUSES
_PROGRESS_RANK = {status: index for index, status in enumerate(PROGRESS_STATUSES)}


class IdempotencyConflictError(ValueError):
    pass


class InvalidLifecycleTransition(ValueError):
    pass


@dataclass(frozen=True)
class PublicSummaryRequest:
    id: UUID
    tenant_id: UUID
    process_code: str
    idempotency_key: str
    request_fingerprint: str
    status: str
    process_id: UUID | None
    version_id: UUID | None
    summary_id: UUID | None
    error_code: str | None


def request_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_request(row: asyncpg.Record) -> PublicSummaryRequest:
    return PublicSummaryRequest(
        id=row["id"],
        tenant_id=row["tenant_id"],
        process_code=str(row["process_code"]),
        idempotency_key=str(row["idempotency_key"]),
        request_fingerprint=str(row["request_fingerprint"]),
        status=str(row["status"]),
        process_id=row["process_id"],
        version_id=row["version_id"],
        summary_id=row["summary_id"],
        error_code=row["error_code"],
    )


async def create_or_get_summary_request(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    process_code: str,
    idempotency_key: str,
    fingerprint: str,
) -> tuple[PublicSummaryRequest, bool]:
    """Create a durable public request or return the exact idempotent replay."""
    if not idempotency_key.strip():
        raise ValueError("idempotency key must not be empty")

    async with conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO public_summary_requests (
                tenant_id, process_code, idempotency_key, request_fingerprint,
                status, completed_at
            )
            VALUES ($1, $2, $3, $4, 'queued', NULL)
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            tenant_id,
            process_code,
            idempotency_key,
            fingerprint,
        )
        if row is not None:
            return _as_request(row), True

        row = await conn.fetchrow(
            """
            SELECT *
            FROM public_summary_requests
            WHERE tenant_id = $1 AND idempotency_key = $2
            FOR UPDATE
            """,
            tenant_id,
            idempotency_key,
        )
        if row is None:
            raise RuntimeError("idempotent public summary request disappeared")
        if not str(row["request_fingerprint"]) == fingerprint:
            raise IdempotencyConflictError(
                "idempotency key was already used with a different request"
            )
        return _as_request(row), False


async def get_summary_request_for_tenant(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    request_id: UUID,
) -> PublicSummaryRequest | None:
    row = await conn.fetchrow(
        """
        SELECT *
        FROM public_summary_requests
        WHERE id = $1 AND tenant_id = $2
        """,
        request_id,
        tenant_id,
    )
    return _as_request(row) if row is not None else None


def _transition_allowed(current: str, target: str) -> bool:
    if current == target:
        return True
    if current in TERMINAL_STATUSES:
        return False
    if target in TERMINAL_STATUSES:
        return True
    return _PROGRESS_RANK[target] >= _PROGRESS_RANK[current]


async def transition_summary_request(
    conn: asyncpg.Connection,
    *,
    request_id: UUID,
    status: str,
    process_id: UUID | None = None,
    version_id: UUID | None = None,
    summary_id: UUID | None = None,
    source_updated_at=None,
    flags: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> PublicSummaryRequest:
    """Advance one public request without allowing stale work to regress it."""
    if status not in ALL_STATUSES:
        raise InvalidLifecycleTransition(f"unknown public summary status: {status}")

    async with conn.transaction():
        row = await conn.fetchrow(
            "SELECT * FROM public_summary_requests WHERE id = $1 FOR UPDATE",
            request_id,
        )
        if row is None:
            raise LookupError("public summary request does not exist")

        current = str(row["status"])
        if not _transition_allowed(current, status):
            # A retry/reclaimed stale job must never downgrade a terminal or later
            # public state. Returning the current row makes that behavior idempotent.
            return _as_request(row)

        completed_at_sql = "NOW()" if status in TERMINAL_STATUSES else "NULL"
        updated = await conn.fetchrow(
            f"""
            UPDATE public_summary_requests
            SET status = $2::public_summary_status,
                process_id = COALESCE($3, process_id),
                version_id = COALESCE($4, version_id),
                summary_id = COALESCE($5, summary_id),
                source_updated_at = COALESCE($6, source_updated_at),
                flags = CASE WHEN $7::jsonb IS NULL THEN flags ELSE flags || $7::jsonb END,
                error_code = $8,
                completed_at = {completed_at_sql},
                updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            request_id,
            status,
            process_id,
            version_id,
            summary_id,
            source_updated_at,
            json.dumps(flags) if flags is not None else None,
            error_code,
        )
        if updated is None:
            raise RuntimeError("public summary request update returned no row")
        return _as_request(updated)
