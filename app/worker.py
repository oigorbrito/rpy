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

from app.db import create_pool
from app.queue import claim, complete, fail, heartbeat, reclaim_stale
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


class Worker:
    def __init__(self, pool: asyncpg.Pool, settings: WorkerSettings, worker_id: UUID | None = None):
        self.pool = pool
        self.settings = settings
        self.worker_id = worker_id or uuid4()
        self.stop_event = asyncio.Event()

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
        payload: dict[str, Any] = dict(row["payload"] or {})
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
            processed = await self.process_one()
            if not processed:
                try:
                    await asyncio.wait_for(
                        self.stop_event.wait(), timeout=self.settings.poll_interval_seconds
                    )
                except TimeoutError:
                    pass

    async def _reclaimer_loop(self) -> None:
        while not self.stop_event.is_set():
            async with self.pool.acquire() as conn:
                reclaimed = await reclaim_stale(conn, self.settings.stale_after_seconds)
            if reclaimed:
                logger.warning("reclaimed %s stale jobs", len(reclaimed))
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
        tasks.append(asyncio.create_task(self._reclaimer_loop()))
        try:
            await self.stop_event.wait()
        finally:
            self.stop_event.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def _install_signal_handlers(worker: Worker) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.stop_event.set)


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Rpy PostgreSQL worker")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    settings = WorkerSettings.from_env()
    pool = await create_pool(settings.database_url, max_size=max(4, settings.concurrency + 2))
    worker = Worker(pool, settings)
    _install_signal_handlers(worker)
    try:
        await worker.run()
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(_main())
