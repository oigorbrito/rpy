from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg

import app.judit_tasks  # noqa: F401 - imports task registrations
import app.rag  # noqa: F401 - imports task registrations
from app.db import create_pool
from app.json_utils import decode_json_object
from app.queue import WAKE_CHANNEL, claim, complete, fail, heartbeat, reclaim_stale
from app.tasks import resolve_task

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkerSettings:
    database_url: str
    concurrency: int = 2
    poll_interval_seconds: float = 1.0
    heartbeat_interval_seconds: float = 10.0
    stale_after_seconds: int = 45
    task_timeout_seconds: float = 120.0
    reclaim_interval_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        return cls(
            database_url=database_url,
            concurrency=int(os.getenv("WORKER_CONCURRENCY", "2")),
            poll_interval_seconds=float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "1")),
            heartbeat_interval_seconds=float(os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "10")),
            stale_after_seconds=int(os.getenv("WORKER_STALE_AFTER_SECONDS", "45")),
            task_timeout_seconds=float(os.getenv("WORKER_TASK_TIMEOUT_SECONDS", "120")),
            reclaim_interval_seconds=float(os.getenv("WORKER_RECLAIM_INTERVAL_SECONDS", "15")),
        )


def _decode_payload(value: Any) -> dict[str, Any]:
    return decode_json_object(value, label="job payload")


class Worker:
    def __init__(self, pool: asyncpg.Pool, settings: WorkerSettings, worker_id: UUID | None = None):
        self.pool = pool
        self.settings = settings
        self.worker_id = worker_id or uuid4()
        self.stop_event = asyncio.Event()
        self.wake_event = asyncio.Event()

    async def _heartbeat_loop(self, job_id: UUID) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(self.settings.heartbeat_interval_seconds)
            async with self.pool.acquire() as conn:
                alive = await heartbeat(conn, job_id, self.worker_id)
            if not alive:
                return

    async def _run_job(self, row: asyncpg.Record) -> None:
        job_id = row["id"]
        handler = resolve_task(str(row["task_name"]))
        payload = _decode_payload(row["payload"])
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job_id))
        try:
            result = await asyncio.wait_for(
                handler(payload), timeout=self.settings.task_timeout_seconds
            )
            async with self.pool.acquire() as conn:
                await complete(conn, job_id, self.worker_id, result or {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("job %s failed", job_id)
            async with self.pool.acquire() as conn:
                await fail(
                    conn,
                    job_id,
                    self.worker_id,
                    attempts=int(row["attempts"]),
                    error=f"{type(exc).__name__}: {exc}",
                )
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task

    async def process_one(self) -> bool:
        async with self.pool.acquire() as conn:
            row = await claim(conn, self.worker_id)
        if row is None:
            return False
        await self._run_job(row)
        return True

    async def _slot_loop(self, slot: int) -> None:
        logger.info("worker %s slot %s started", self.worker_id, slot)
        while not self.stop_event.is_set():
            # Clear before claim to avoid losing a notification that races with an
            # empty claim. If a job commits after this clear, wake_event stays set.
            self.wake_event.clear()
            processed = await self.process_one()
            if processed:
                continue
            try:
                await asyncio.wait_for(
                    self.wake_event.wait(), timeout=self.settings.poll_interval_seconds
                )
            except TimeoutError:
                # Polling is the correctness fallback if LISTEN/NOTIFY is lost.
                pass

    async def _listener_loop(self) -> None:
        reconnect_delay = min(5.0, max(0.1, self.settings.poll_interval_seconds))
        while not self.stop_event.is_set():
            conn: asyncpg.Connection | None = None
            terminated = asyncio.Event()

            def on_notification(
                connection: asyncpg.Connection,
                pid: int,
                channel: str,
                payload: str,
            ) -> None:
                del connection, pid, channel, payload
                self.wake_event.set()

            def on_termination(connection: asyncpg.Connection) -> None:
                del connection
                terminated.set()
                self.wake_event.set()

            try:
                conn = await asyncpg.connect(self.settings.database_url)
                await conn.add_listener(WAKE_CHANNEL, on_notification)
                conn.add_termination_listener(on_termination)
                logger.info("worker %s listening on %s", self.worker_id, WAKE_CHANNEL)

                stop_wait = asyncio.create_task(self.stop_event.wait())
                termination_wait = asyncio.create_task(terminated.wait())
                done, pending = await asyncio.wait(
                    {stop_wait, termination_wait},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for item in pending:
                    item.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if stop_wait in done and self.stop_event.is_set():
                    return
                logger.warning("worker %s queue listener disconnected; reconnecting", self.worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "worker %s queue listener failed; polling fallback remains active",
                    self.worker_id,
                )
            finally:
                if conn is not None and not conn.is_closed():
                    with suppress(Exception):
                        await conn.remove_listener(WAKE_CHANNEL, on_notification)
                    with suppress(Exception):
                        await conn.close()

            if not self.stop_event.is_set():
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=reconnect_delay)
                except TimeoutError:
                    pass

    async def _reclaimer_loop(self) -> None:
        while not self.stop_event.is_set():
            async with self.pool.acquire() as conn:
                reclaimed = await reclaim_stale(conn, self.settings.stale_after_seconds)
            if reclaimed:
                logger.warning("reclaimed %s stale jobs", len(reclaimed))
                self.wake_event.set()
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=self.settings.reclaim_interval_seconds
                )
            except TimeoutError:
                pass

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(self._slot_loop(slot))
            for slot in range(self.settings.concurrency)
        ]
        tasks.append(asyncio.create_task(self._listener_loop()))
        tasks.append(asyncio.create_task(self._reclaimer_loop()))
        try:
            await self.stop_event.wait()
        finally:
            self.wake_event.set()
            for item in tasks:
                item.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Rpy PostgreSQL worker")
    parser.add_argument("--concurrency", type=int)
    args = parser.parse_args()

    settings = WorkerSettings.from_env()
    if args.concurrency is not None:
        settings.concurrency = args.concurrency

    pool = await create_pool(
        settings.database_url,
        min_size=1,
        max_size=max(4, settings.concurrency + 2),
    )
    worker = Worker(pool, settings)
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, worker.stop)

    try:
        await worker.run()
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(_main())
