from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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


@pytest.mark.asyncio
async def test_operational_metrics_are_aggregate_and_protected(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    app.state.pool = pool
    monkeypatch.setenv("RPY_OPS_TOKEN", "ops-secret")

    old = datetime.now(UTC) - timedelta(minutes=5)
    process_id = uuid4()
    version_id = uuid4()
    code = "0000000-00.0000.0.00.0301"

    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE jobs, process_summaries, process_steps, tenant_processes, process_versions, processes RESTART IDENTITY CASCADE")
            await conn.execute(
                """
                INSERT INTO jobs (task_name, payload, status, created_at, updated_at)
                VALUES
                  ('a', '{}'::jsonb, 'pending', $1, $1),
                  ('b', '{}'::jsonb, 'dead', NOW(), NOW()),
                  ('c', '{}'::jsonb, 'completed', NOW(), NOW()),
                  ('d', '{}'::jsonb, 'processing', NOW(), NOW())
                """,
                old,
            )
            await conn.execute(
                "UPDATE jobs SET last_heartbeat = NOW() - interval '120 seconds' WHERE task_name = 'd'"
            )
            await conn.execute(
                "INSERT INTO processes (id, code) VALUES ($1, $2)",
                process_id,
                code,
            )
            await conn.execute(
                """
                INSERT INTO process_versions (id, process_id, source_request_id, source_payload, finalized)
                VALUES ($1, $2, 'obs', '{}'::jsonb, TRUE)
                """,
                version_id,
                process_id,
            )
            await conn.execute(
                "UPDATE processes SET current_version_id = $2 WHERE id = $1",
                process_id,
                version_id,
            )
            await conn.execute(
                """
                INSERT INTO process_summaries (
                    process_id, version_id, markdown, validation, model, prompt_version, generation_ms
                ) VALUES ($1, $2, 'summary', $3::jsonb, 'model', 'prompt', 250)
                """,
                process_id,
                version_id,
                '{"passed": false, "errors": ["x"]}',
            )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            hidden = await client.get("/ops/metrics")
            wrong = await client.get(
                "/ops/metrics", headers={"Authorization": "Bearer wrong"}
            )
            response = await client.get(
                "/ops/metrics", headers={"Authorization": "Bearer ops-secret"}
            )

        assert hidden.status_code == 404
        assert wrong.status_code == 404
        assert response.status_code == 200
        body = response.json()
        assert body["queue"] == {
            "pending": 1,
            "processing": 1,
            "completed": 1,
            "dead": 1,
        }
        assert body["oldest_pending_seconds"] >= 240
        assert body["stale_processing"] == 1
        assert body["summaries"]["total"] == 1
        assert body["summaries"]["validation_failed"] == 1
        assert body["summaries"]["validation_failure_rate"] == 1.0
        assert body["summaries"]["avg_generation_ms"] == 250.0
        assert body["summaries"]["p95_generation_ms"] == 250.0
        serialized = str(body)
        assert code not in serialized
        assert "summary" not in serialized
    finally:
        await pool.close()
