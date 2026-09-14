from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.db import create_pool
from app.queue import claim, enqueue, reclaim_stale
from scripts.migrate import migrate

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture(scope="module", autouse=True)
async def database() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute("TRUNCATE jobs RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_only_one_worker_claims_a_job() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=4)
    try:
        async with pool.acquire() as conn:
            await enqueue(
                conn,
                task_name="integration-test",
                payload={"value": 1},
                idempotency_key="integration:claim-once",
            )

        worker_a = uuid4()
        worker_b = uuid4()

        async def do_claim(worker_id):
            async with pool.acquire() as conn:
                return await claim(conn, worker_id)

        first, second = await asyncio.gather(
            do_claim(worker_a),
            do_claim(worker_b),
        )
        claimed = [row for row in (first, second) if row is not None]
        assert len(claimed) == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_idempotent_enqueue_does_not_duplicate_job() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            first = await enqueue(
                conn,
                task_name="integration-test",
                payload={"value": 2},
                idempotency_key="integration:idem",
            )
            second = await enqueue(
                conn,
                task_name="integration-test",
                payload={"value": 2},
                idempotency_key="integration:idem",
            )
        assert first is not None
        assert second is None
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_reclaimer_returns_stale_job_to_pending() -> None:
    assert TEST_DATABASE_URL is not None
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    worker_id = uuid4()
    try:
        async with pool.acquire() as conn:
            await enqueue(
                conn,
                task_name="integration-test",
                payload={"value": 3},
                idempotency_key="integration:reclaim",
            )
            row = await claim(conn, worker_id)
            assert row is not None
            stale_at = datetime.now(UTC) - timedelta(minutes=10)
            await conn.execute(
                "UPDATE jobs SET last_heartbeat = $2 WHERE id = $1",
                row["id"],
                stale_at,
            )
            reclaimed = await reclaim_stale(conn, timeout_seconds=30)
            status = await conn.fetchval("SELECT status FROM jobs WHERE id = $1", row["id"])

        assert any(item["id"] == row["id"] for item in reclaimed)
        assert str(status) == "pending"
    finally:
        await pool.close()
