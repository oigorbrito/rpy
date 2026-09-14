from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException, Request

from app.db import create_pool
from app.judit import extract_promotable_fields, parse_event
from app.processes import finalize_version, stage_version
from app.queue import enqueue


@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    app.state.pool = await create_pool(database_url)
    try:
        yield
    finally:
        await app.state.pool.close()


app = FastAPI(title="Rpy", lifespan=lifespan)


def _valid_webhook_token(token: str) -> bool:
    expected = os.environ.get("JUDIT_WEBHOOK_TOKEN", "")
    return bool(expected) and hmac.compare_digest(token, expected)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/webhooks/judit/{token}")
async def judit_webhook(token: str, request: Request) -> dict[str, bool]:
    if not _valid_webhook_token(token):
        raise HTTPException(status_code=404, detail="not found")

    try:
        payload = await request.json()
        event = parse_event(payload)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="invalid payload") from None

    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        process_id, version_id = await stage_version(
            conn,
            code=event.code,
            source_request_id=event.request_id,
            cached_response=event.cached_response,
            payload=event.raw,
        )

        if event.request_completed:
            fields = extract_promotable_fields(event.raw)
            await finalize_version(
                conn,
                process_id=process_id,
                version_id=version_id,
                **fields,
            )

            if not event.cached_response:
                await enqueue(
                    conn,
                    task_name="generate_process_summary",
                    payload={
                        "process_id": str(process_id),
                        "version_id": str(version_id),
                        "code": event.code,
                    },
                    idempotency_key=f"summary:{version_id}",
                )

    return {"ok": True}
