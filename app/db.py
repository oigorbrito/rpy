from __future__ import annotations

import asyncpg


async def create_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=database_url,
        min_size=min_size,
        max_size=max_size,
        command_timeout=60,
    )
