from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import asyncpg
import pytest

from app.db import create_pool
from app.migrations import migrate
from app.queue import WAKE_CHANNEL, enqueue
from app.tasks import task
from app.worker import Worker, WorkerSettings

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_enqueue_notifies_only_for_new_job() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    listener = await asyncpg.connect(TEST_DATABASE_URL)
    received: asyncio.Queue[str] = asyncio.Queue()

    def callback(
        connection: asyncpg.Connection,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        del connection, pid
        assert channel == WAKE_CHANNEL
        received.put_nowait(payload)

    await listener.add_listener(WAKE_CHANNEL, callback)
    try:
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE jobs RESTART IDENTITY CASCADE")
            row = await enqueue(
                conn,
                task_name="notify-test",
                payload={"ok": True},
                idempotency_key="notify-once",
            )
        assert row is not None
        payload = await asyncio.wait_for(received.get(), timeout=2.0)
        assert payload == str(row["id"])

        async with pool.acquire() as conn:
            duplicate = await enqueue(
                conn,
                task_name="notify-test",
                payload={"ok": True},
                idempotency_key="notify-once",
            )
        assert duplicate is None
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(received.get(), timeout=0.25)
    finally:
        await listener.remove_listener(WAKE_CHANNEL, callback)
        await listener.close()
        await pool.close()


@pytest.mark.asyncio
async def test_worker_wakes_without_waiting_for_long_poll_interval() -> None:
    assert TEST_DATABASE_URL is not None
    await migrate(TEST_DATABASE_URL)
    pool = await create_pool(TEST_DATABASE_URL, min_size=2, max_size=6)
    executed = asyncio.Event()
    task_name = f"wake-test-{uuid4()}"

    @task(task_name)
    async def wake_test_task(payload: dict) -> dict:
        assert payload == {"marker": "wake"}
        executed.set()
        return {"done": True}

    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE jobs RESTART IDENTITY CASCADE")

    worker = Worker(
        pool,
        WorkerSettings(
            database_url=TEST_DATABASE_URL,
            concurrency=1,
            poll_interval_seconds=30.0,
            heartbeat_interval_seconds=60.0,
            stale_after_seconds=120,
            task_timeout_seconds=5.0,
            reclaim_interval_seconds=60.0,
        ),
    )
    runner = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(worker.listener_ready.wait(), timeout=2.0)

        async with pool.acquire() as conn:
            row = await enqueue(
                conn,
                task_name=task_name,
                payload={"marker": "wake"},
                idempotency_key=f"wake-{uuid4()}",
            )
        assert row is not None

        await asyncio.wait_for(executed.wait(), timeout=2.0)
        async with pool.acquire() as conn:
            status = await conn.fetchval("SELECT status::text FROM jobs WHERE id = $1", row["id"])
        assert status == "completed"
    finally:
        worker.stop()
        await asyncio.wait_for(runner, timeout=3.0)
        await pool.close()
