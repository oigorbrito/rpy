from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

import app.process_requests as process_requests
from app.api import app
from app.db import create_pool
from app.judit_client import JuditRequestResult
from app.migrations import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is required")
CNJ = "0000000-00.2024.8.26.0100"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "judit" / "tracking_lawsuit_response.json"

async def _setup(monkeypatch):
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL); pool = await create_pool(TEST_DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE jobs, judit_deliveries, judit_request_completions, process_summaries, process_steps, tenant_processes, tenant_judit_requests, access_log, process_versions, processes, tenants RESTART IDENTITY CASCADE")
    tenant_id=uuid4(); token=f"request-{uuid4()}"; app.state.bearer_tokens={token:tenant_id}; app.state.pool=pool
    async with pool.acquire() as conn: await conn.execute("INSERT INTO tenants(id,name) VALUES($1,'tenant')",tenant_id)
    return pool,tenant_id,token

@pytest.mark.asyncio
async def test_request_is_idempotent_and_grants_callback_process(monkeypatch):
    pool,tenant_id,token=await _setup(monkeypatch); calls=0
    async def fake_create(code):
        nonlocal calls; calls+=1; assert code==CNJ
        return JuditRequestResult(request_id="req-tenant-1")
    monkeypatch.setattr(process_requests,"create_lawsuit_request",fake_create)
    transport=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
        headers={"Authorization":f"Bearer {token}"}
        first=await client.post(f"/processes/{CNJ}/request",headers=headers); second=await client.post(f"/processes/{CNJ}/request",headers=headers)
        assert first.status_code==second.status_code==202; assert first.json()["created"] is True; assert second.json()["created"] is False; assert calls==1
        payload=deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))); payload["callback_id"]="cb-tenant-1"; payload["payload"]["request_id"]="req-tenant-1"; payload["payload"]["response_id"]="resp-tenant-1"; payload["payload"]["response_data"]["code"]=CNJ
        monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN","webhook-secret")
        callback=await client.post("/webhooks/judit/webhook-secret",json=payload); assert callback.status_code==200
    async with pool.acquire() as conn:
        count=await conn.fetchval("SELECT count(*) FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",tenant_id,CNJ)
        granted=await conn.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_processes tp JOIN processes p ON p.id=tp.process_id WHERE tp.tenant_id=$1 AND p.code=$2)",tenant_id,CNJ)
        audit=await conn.fetchval("SELECT count(*) FROM access_log WHERE tenant_id=$1 AND action='request_process'",tenant_id)
    assert count==1 and granted and audit==2; await pool.close()

@pytest.mark.asyncio
async def test_existing_process_is_granted_without_provider_call(monkeypatch):
    pool,tenant_id,token=await _setup(monkeypatch)
    async def fail_create(code): raise AssertionError("provider must not be called")
    monkeypatch.setattr(process_requests,"create_lawsuit_request",fail_create)
    async with pool.acquire() as conn: await conn.execute("INSERT INTO processes(code) VALUES($1)",CNJ)
    transport=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,base_url="http://test") as client: response=await client.post(f"/processes/{CNJ}/request",headers={"Authorization":f"Bearer {token}"})
    assert response.status_code==202; assert response.json()=={"code":CNJ,"status":"available","created":False}
    async with pool.acquire() as conn: assert await conn.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_processes WHERE tenant_id=$1)",tenant_id)
    await pool.close()
