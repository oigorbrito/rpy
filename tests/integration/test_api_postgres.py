from __future__ import annotations

import json
import os
from uuid import uuid4

import asyncpg
import httpx
import pytest

from app.api import app
from app.db import create_pool
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture
async def api_client(monkeypatch: pytest.MonkeyPatch):
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=4)
    app.state.pool = pool
    monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN", "integration-webhook")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, pool
    await pool.close()


@pytest.mark.asyncio
async def test_invalid_webhook_token_is_404(api_client) -> None:
    client, _ = api_client
    response = await client.post(
        "/webhooks/judit/wrong-token",
        json={"event_type": "request_completed", "reference_id": "req-x", "payload": {}},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_response_created_is_staged_without_summary_job(api_client) -> None:
    client, pool = api_client
    request_id = f"req-{uuid4()}"
    response_id = f"resp-{uuid4()}"
    callback_id = f"cb-{uuid4()}"
    code = "0000000-00.0000.0.00.0101"

    response = await client.post(
        "/webhooks/judit/integration-webhook",
        json={
            "callback_id": callback_id,
            "event_type": "response_created",
            "reference_type": "request",
            "reference_id": request_id,
            "payload": {
                "request_id": request_id,
                "response_id": response_id,
                "response_type": "lawsuit",
                "response_data": {"code": code, "steps": []},
                "tags": {"cached_response": False},
            },
        },
    )
    assert response.status_code == 200

    async with pool.acquire() as conn:
        staged = await conn.fetchrow(
            "SELECT judit_request_id, judit_response_id, finalized FROM process_versions WHERE judit_response_id = $1",
            response_id,
        )
        summary_jobs = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE task_name = 'generate_process_summary' AND payload->>'code' = $1",
            code,
        )
    assert staged is not None
    assert staged["judit_request_id"] == request_id
    assert staged["finalized"] is False
    assert summary_jobs == 0


@pytest.mark.asyncio
async def test_request_completed_only_enqueues_finalizer_and_is_idempotent(api_client) -> None:
    client, pool = api_client
    request_id = f"req-{uuid4()}"
    callback_id = f"cb-{uuid4()}"
    body = {
        "callback_id": callback_id,
        "event_type": "request_completed",
        "reference_type": "request",
        "reference_id": request_id,
        "payload": {"status": "completed"},
    }

    first = await client.post("/webhooks/judit/integration-webhook", json=body)
    second = await client.post("/webhooks/judit/integration-webhook", json=body)
    assert first.status_code == 200
    assert second.status_code == 200

    async with pool.acquire() as conn:
        deliveries = await conn.fetchval(
            "SELECT count(*) FROM judit_deliveries WHERE callback_id = $1",
            callback_id,
        )
        jobs = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE idempotency_key = $1",
            f"judit-finalize:{request_id}",
        )
    assert deliveries == 1
    assert jobs == 1


@pytest.mark.asyncio
async def test_process_read_is_tenant_scoped_and_audited(api_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, pool = api_client
    allowed_tenant = uuid4()
    denied_tenant = uuid4()
    process_id = uuid4()
    code = "0000000-00.0000.0.00.0102"

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, 'allowed'), ($2, 'denied')",
            allowed_tenant,
            denied_tenant,
        )
        await conn.execute(
            "INSERT INTO processes (id, code, class_name, court) VALUES ($1, $2, 'Classe', 'TJ')",
            process_id,
            code,
        )
        await conn.execute(
            "INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1, $2)",
            allowed_tenant,
            process_id,
        )

    monkeypatch.setenv(
        "RPY_BEARER_TOKENS",
        json.dumps({"allowed-token": str(allowed_tenant), "denied-token": str(denied_tenant)}),
    )

    denied = await client.get(
        f"/processes/{code}",
        headers={"Authorization": "Bearer denied-token"},
    )
    assert denied.status_code == 404

    allowed = await client.get(
        f"/processes/{code}",
        headers={"Authorization": "Bearer allowed-token"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["code"] == code

    async with pool.acquire() as conn:
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM access_log WHERE tenant_id = $1 AND process_code = $2 AND action = 'read_process_summary'",
            allowed_tenant,
            code,
        )
    assert audit_count == 1
