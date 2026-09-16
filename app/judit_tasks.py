from __future__ import annotations

import os
from typing import Any

from app.db import create_pool
from app.json_utils import decode_json_object
from app.judit import extract_promotable_fields, parse_event
from app.processes import finalize_version, preferred_judit_version
from app.public_lifecycle import transition_requests_for_judit_request
from app.queue import enqueue
from app.tasks import task


async def _complete_from_current_summary(
    conn,
    *,
    request_id: str,
    process_id,
) -> bool:
    current = await conn.fetchrow(
        """
        SELECT p.current_version_id AS version_id,
               ps.id AS summary_id,
               p.updated_at AS source_updated_at
        FROM processes p
        LEFT JOIN process_summaries ps
          ON ps.process_id = p.id
         AND ps.version_id = p.current_version_id
         AND COALESCE((ps.validation->>'passed')::boolean, false) = true
        WHERE p.id = $1
        """,
        process_id,
    )
    if current is None or current["version_id"] is None or current["summary_id"] is None:
        return False
    await transition_requests_for_judit_request(
        conn,
        judit_request_id=request_id,
        status="completed",
        process_id=process_id,
        version_id=current["version_id"],
        summary_id=current["summary_id"],
        source_updated_at=current["source_updated_at"],
        flags={"reused_existing_summary": True},
    )
    return True


@task("finalize_judit_request")
async def finalize_judit_request_task(payload: dict[str, Any]) -> dict[str, Any]:
    request_id = str(payload["request_id"])
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    pool = await create_pool(database_url, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            # Promotion and summary enqueue are one durable unit. finalize_version()
            # uses a nested transaction/savepoint, so an enqueue failure rolls the
            # entire promotion back and lets the finalizer job retry safely.
            async with conn.transaction():
                await transition_requests_for_judit_request(
                    conn,
                    judit_request_id=request_id,
                    status="indexing",
                )
                staged = await preferred_judit_version(conn, request_id=request_id)
                if staged is None:
                    await transition_requests_for_judit_request(
                        conn,
                        judit_request_id=request_id,
                        status="source_unavailable",
                        error_code="no_lawsuit_response",
                    )
                    return {"request_id": request_id, "status": "no_lawsuit_response"}

                source_payload = decode_json_object(
                    staged["source_payload"], label="staged Judit source payload"
                )
                staged_event = parse_event(source_payload)
                if not staged_event.response_data:
                    await transition_requests_for_judit_request(
                        conn,
                        judit_request_id=request_id,
                        status="source_unavailable",
                        error_code="missing_response_data",
                    )
                    return {"request_id": request_id, "status": "missing_response_data"}

                fields = extract_promotable_fields(staged_event.response_data)
                promoted = await finalize_version(
                    conn,
                    process_id=staged["process_id"],
                    version_id=staged["version_id"],
                    **fields,
                )

                equivalent_to_version_id = None
                if not promoted:
                    equivalent_to_version_id = await conn.fetchval(
                        """
                        SELECT equivalent_to_version_id
                        FROM process_versions
                        WHERE id = $1 AND process_id = $2
                        """,
                        staged["version_id"],
                        staged["process_id"],
                    )

                summary_enqueued = False
                if promoted:
                    await transition_requests_for_judit_request(
                        conn,
                        judit_request_id=request_id,
                        status="indexing",
                        process_id=staged["process_id"],
                        version_id=staged["version_id"],
                    )

                if promoted and not bool(staged["source_cached_response"]):
                    job = await enqueue(
                        conn,
                        task_name="generate_process_summary",
                        payload={
                            "process_id": str(staged["process_id"]),
                            "version_id": str(staged["version_id"]),
                            "code": staged["code"],
                            "judit_request_id": request_id,
                        },
                        idempotency_key=f"summary:{staged['version_id']}",
                    )
                    summary_enqueued = job is not None
                elif await _complete_from_current_summary(
                    conn,
                    request_id=request_id,
                    process_id=staged["process_id"],
                ):
                    pass
                else:
                    error_code = (
                        "cached_response_not_generated"
                        if promoted and bool(staged["source_cached_response"])
                        else "current_summary_unavailable"
                    )
                    await transition_requests_for_judit_request(
                        conn,
                        judit_request_id=request_id,
                        status="failed",
                        process_id=staged["process_id"],
                        version_id=(
                            staged["version_id"]
                            if promoted
                            else equivalent_to_version_id
                        ),
                        error_code=error_code,
                    )

        if promoted:
            status = "finalized"
        elif equivalent_to_version_id is not None:
            status = "finalized_unchanged"
        else:
            status = "finalized_stale"
        return {
            "request_id": request_id,
            "status": status,
            "promoted": promoted,
            "cached_response": bool(staged["source_cached_response"]),
            "summary_enqueued": summary_enqueued,
            "equivalent_to_version_id": (
                str(equivalent_to_version_id)
                if equivalent_to_version_id is not None
                else None
            ),
        }
    finally:
        await pool.close()
