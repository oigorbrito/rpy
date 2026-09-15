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
    async with pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE jobs, judit_deliveries, process_summaries, process_steps,
                     tenant_processes, access_log, process_versions, processes,
                     tenants
            RESTART IDENTITY CASCADE
            """
        )
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
async def test_webhook_with_tenant_binding_grants_carteira(api_client, monkeypatch: pytest.MonkeyPatch) -> None:
    """JUDIT_WEBHOOK_TENANT_ID binds ingested processes to the tenant portfolio."""
    client, pool = api_client
    tenant_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, name) VALUES ($1, 'webhook-bounded')",
            tenant_id,
        )
    monkeypatch.setenv("JUDIT_WEBHOOK_TENANT_ID", str(tenant_id))
    monkeypatch.setattr(app.state, "webhook_tenant_id", tenant_id, raising=False)

    await client.post(
        "/webhooks/judit/integration-webhook",
        json={
            "callback_id": f"cb-tenant-{uuid4()}",
            "event_type": "response_created",
            "reference_type": "request",
            "reference_id": f"req-tenant-{uuid4()}",
            "payload": {
                "request_id": f"req-tenant-{uuid4()}",
                "response_id": f"resp-tenant-{uuid4()}",
                "response_type": "lawsuit",
                "response_data": {
                    "code": "0000000-00.0000.0.00.0103",
                    "classifications": [{"name": "Execução Fiscal"}],
                    "parties": [{"name": "Município de São Paulo", "person_type": "JURIDICA"}],
                },
            },
        },
    )

    async with pool.acquire() as conn:
        process_id = await conn.fetchval(
            "SELECT id FROM processes WHERE code = '0000000-00.0000.0.00.0103'"
        )
        assert process_id is not None
        bound = await conn.fetchval(
            "SELECT process_id FROM tenant_processes WHERE tenant_id = $1 AND process_id = $2",
            tenant_id,
            process_id,
        )
        assert bound == process_id

    monkeypatch.setenv(
        "RPY_BEARER_TOKENS",
        json.dumps({f"tenant-token-{tenant_id}": str(tenant_id)}),
    )
    response = await client.get(
        "/processes/0000000-00.0000.0.00.0103",
        headers={"Authorization": f"Bearer tenant-token-{tenant_id}"},
    )
    assert response.status_code == 200
    assert response.json()["code"] == "0000000-00.0000.0.00.0103"


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
async def test_tracking_application_info_enqueues_finalizer_for_payload_request_id(api_client) -> None:
    client, pool = api_client
    request_id = f"req-{uuid4()}"
    tracking_id = f"tracking-{uuid4()}"
    callback_id = f"cb-{uuid4()}"

    response = await client.post(
        "/webhooks/judit/integration-webhook",
        json={
            "callback_id": callback_id,
            "event_type": "response_created",
            "reference_type": "tracking",
            "reference_id": tracking_id,
            "payload": {
                "request_id": request_id,
                "response_id": f"resp-{uuid4()}",
                "response_type": "application_info",
                "response_data": {"code": 600, "message": "REQUEST_COMPLETED"},
                "tags": {"cached_response": False},
            },
        },
    )
    assert response.status_code == 200

    async with pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT payload, idempotency_key FROM jobs WHERE idempotency_key = $1",
            f"judit-finalize:{request_id}",
        )
        tracking_job_count = await conn.fetchval(
            "SELECT count(*) FROM jobs WHERE idempotency_key = $1",
            f"judit-finalize:{tracking_id}",
        )
    assert job is not None
    assert job["idempotency_key"] == f"judit-finalize:{request_id}"
    assert tracking_job_count == 0


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


@pytest.mark.asyncio
async def test_failed_summaries_endpoint_is_ops_scoped(api_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, pool = api_client

    no_token = await client.get("/ops/failed-summaries")
    assert no_token.status_code == 404

    monkeypatch.setenv("RPY_OPS_TOKEN", "ops-secret")
    wrong_token = await client.get(
        "/ops/failed-summaries", headers={"Authorization": "Bearer wrong"}
    )
    assert wrong_token.status_code == 404

    code = f"0000000-00.0000.0.00.{uuid4().hex[:4]}"
    async with pool.acquire() as conn:
        process_id = uuid4()
        version_id = uuid4()
        await conn.execute(
            "INSERT INTO processes (id, code, class_name, court) VALUES ($1, $2, 'Classe', 'TJ')",
            process_id,
            code,
        )
        await conn.execute(
            "INSERT INTO process_versions (id, process_id, source_request_id) VALUES ($1, $2, $3)",
            version_id,
            process_id,
            "req-failed-1",
        )
        await conn.execute(
            """
            INSERT INTO process_summaries
                (process_id, version_id, markdown, validation, model, prompt_version, generation_ms)
            VALUES
                ($1, $2, 'summary', $3::jsonb, 'model-x', 'v1', 120),
                ($1, $2, 'summary2', $4::jsonb, 'model-x', 'v1', 90)
            ON CONFLICT (process_id, version_id) DO NOTHING
            """,
            process_id,
            version_id,
            json.dumps({"passed": False, "errors": ["hallucination"]}),
            json.dumps({"passed": True}),
        )

    with_token = await client.get(
        "/ops/failed-summaries", headers={"Authorization": "Bearer ops-secret"}
    )
    assert with_token.status_code == 200
    rows = with_token.json()["failed_summaries"]
    mine = [row for row in rows if row["code"] == code]
    assert len(mine) == 1
    assert mine[0]["validation"]["passed"] is False
    assert mine[0]["validation"]["errors"] == ["hallucination"]
