from __future__ import annotations

import os
from typing import Any

from app.db import create_pool
from app.json_utils import decode_json_object
from app.judit import extract_promotable_fields, parse_event
from app.processes import finalize_version, preferred_judit_version
from app.queue import enqueue
from app.tasks import task


@task("finalize_judit_request")
async def finalize_judit_request_task(payload: dict[str, Any]) -> dict[str, Any]:
    request_id = str(payload["request_id"])
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    pool = await create_pool(database_url, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            staged = await preferred_judit_version(conn, request_id=request_id)
            if staged is None:
                return {"request_id": request_id, "status": "no_lawsuit_response"}

            source_payload = decode_json_object(
                staged["source_payload"], label="staged Judit source payload"
            )
            staged_event = parse_event(source_payload)
            if not staged_event.response_data:
                return {"request_id": request_id, "status": "missing_response_data"}

            fields = extract_promotable_fields(staged_event.response_data)
            await finalize_version(
                conn,
                process_id=staged["process_id"],
                version_id=staged["version_id"],
                **fields,
            )

            summary_enqueued = False
            if not bool(staged["source_cached_response"]):
                job = await enqueue(
                    conn,
                    task_name="generate_process_summary",
                    payload={
                        "process_id": str(staged["process_id"]),
                        "version_id": str(staged["version_id"]),
                        "code": staged["code"],
                    },
                    idempotency_key=f"summary:{staged['version_id']}",
                )
                summary_enqueued = job is not None

        return {
            "request_id": request_id,
            "status": "finalized",
            "cached_response": bool(staged["source_cached_response"]),
            "summary_enqueued": summary_enqueued,
        }
    finally:
        await pool.close()
