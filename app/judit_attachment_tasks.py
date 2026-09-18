from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from app.attachment_context import resolve_generation_tenant
from app.attachment_processing import attachment_processing_limits, process_attachment_bytes
from app.attachments import upsert_attachment_state
from app.db import create_pool
from app.judit_client import (
    JuditRequestError,
    download_signed_attachment,
    get_lawsuit_attachment_url,
)
from app.queue import enqueue
from app.tasks import task


_EXTENSION_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain",
    "text": "text/plain",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


def _effective_content_type(content_type: str, extension: object) -> str:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized in {"application/pdf", "text/plain", "image/png", "image/jpeg"}:
        return normalized
    suffix = str(extension or "").strip().lower().lstrip(".")
    return _EXTENSION_CONTENT_TYPES.get(suffix, normalized or "application/octet-stream")


async def _mark_unavailable(
    conn,
    *,
    process_id: UUID,
    version_id: UUID,
    attachment_id: str,
    error_code: str,
) -> None:
    await upsert_attachment_state(
        conn,
        process_id=process_id,
        version_id=version_id,
        source_attachment_id=attachment_id,
        status="unavailable",
        error_code=error_code,
    )


@task("process_judit_attachments")
async def process_judit_attachments_task(payload: dict[str, Any]) -> dict[str, Any]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    process_id = UUID(str(payload["process_id"]))
    version_id = UUID(str(payload["version_id"]))
    request_id = str(payload["judit_request_id"])
    code = str(payload["code"])
    raw_refs = payload.get("attachments")
    if not isinstance(raw_refs, list):
        raise ValueError("attachments payload must be a list")

    pool = await create_pool(database_url, min_size=1, max_size=3)
    processed = 0
    unavailable = 0
    skipped = 0
    try:
        tenant_id = await resolve_generation_tenant(
            pool,
            judit_request_id=request_id,
            process_id=process_id,
        )
        async with pool.acquire() as conn:
            process_ok = bool(
                await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM processes
                        WHERE id=$1
                          AND current_version_id=$2
                          AND secrecy_level=0
                    )
                    """,
                    process_id,
                    version_id,
                )
            )

        limits = attachment_processing_limits()
        for raw in raw_refs:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            source_id = str(raw.get("attachment_id") or "").strip()
            status = str(raw.get("status") or "").strip().lower()
            try:
                instance = int(raw.get("instance"))
            except (TypeError, ValueError):
                instance = 0
            if not source_id:
                skipped += 1
                continue
            if status != "done":
                skipped += 1
                continue

            if tenant_id is None or not process_ok or instance <= 0:
                async with pool.acquire() as conn:
                    await _mark_unavailable(
                        conn,
                        process_id=process_id,
                        version_id=version_id,
                        attachment_id=source_id,
                        error_code="attachment_download_unauthorized",
                    )
                unavailable += 1
                continue

            try:
                signed_url = await get_lawsuit_attachment_url(
                    code,
                    instance=instance,
                    attachment_id=source_id,
                )
                download = await download_signed_attachment(
                    signed_url,
                    max_bytes=limits.max_bytes,
                )
            except JuditRequestError:
                async with pool.acquire() as conn:
                    await _mark_unavailable(
                        conn,
                        process_id=process_id,
                        version_id=version_id,
                        attachment_id=source_id,
                        error_code="judit_attachment_unavailable",
                    )
                unavailable += 1
                continue

            async with pool.acquire() as conn:
                result = await process_attachment_bytes(
                    conn,
                    process_id=process_id,
                    version_id=version_id,
                    source_attachment_id=source_id,
                    content_type=_effective_content_type(
                        download.content_type,
                        raw.get("extension"),
                    ),
                    data=download.data,
                    limits=limits,
                )
            if result["status"] == "ready":
                processed += 1
            else:
                unavailable += 1

        async with pool.acquire() as conn:
            job = await enqueue(
                conn,
                task_name="generate_process_summary",
                payload={
                    "process_id": str(process_id),
                    "version_id": str(version_id),
                    "code": code,
                    "judit_request_id": request_id,
                },
                idempotency_key=f"summary:{version_id}",
            )
        return {
            "processed": processed,
            "unavailable": unavailable,
            "skipped": skipped,
            "summary_enqueued": job is not None,
        }
    finally:
        await pool.close()
