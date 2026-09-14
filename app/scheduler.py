from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from datetime import timedelta

import asyncpg

from app.db import create_pool

logger = logging.getLogger(__name__)
LOCK_NAME = "rpy_scheduler"


async def expurgar(conn: asyncpg.Connection, *, retention_days: int) -> int:
    deleted = await conn.fetchval(
        """
        WITH doomed AS (
            SELECT id
            FROM processes
            WHERE updated_at < NOW() - make_interval(days => $1)
        ),
        request_ids AS (
            SELECT DISTINCT judit_request_id
            FROM process_versions
            WHERE process_id IN (SELECT id FROM doomed)
              AND judit_request_id IS NOT NULL
        ),
        deleted_deliveries AS (
            DELETE FROM judit_deliveries
            WHERE request_id IN (SELECT judit_request_id FROM request_ids)
            RETURNING callback_id
        ),
        deleted_processes AS (
            DELETE FROM processes
            WHERE id IN (SELECT id FROM doomed)
            RETURNING id
        )
        SELECT count(*) FROM deleted_processes
        """,
        retention_days,
    )
    return int(deleted or 0)


async def acquire_singleton(conn: asyncpg.Connection) -> bool:
    return bool(await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", LOCK_NAME))


async def release_singleton(conn: asyncpg.Connection) -> None:
    await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", LOCK_NAME)


async def run_scheduler() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    retention_days = int(os.getenv("RETENTION_DAYS", "365"))
    interval_seconds = float(
        os.getenv("EXPUNGE_INTERVAL_SECONDS", str(timedelta(hours=24).total_seconds()))
    )

    pool = await create_pool(database_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as lock_conn:
            if not await acquire_singleton(lock_conn):
                raise RuntimeError(
                    "another Rpy scheduler instance already holds the singleton lock"
                )
            try:
                while True:
                    async with pool.acquire() as conn:
                        deleted = await expurgar(conn, retention_days=retention_days)
                    logger.info("expunge completed: %s process(es) deleted", deleted)
                    await asyncio.sleep(interval_seconds)
            finally:
                with suppress(Exception):
                    await release_singleton(lock_conn)
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    asyncio.run(run_scheduler())
