from __future__ import annotations

from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "sql"


async def migrate(database_url: str) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
            print(f"applied {path.name}")
    finally:
        await conn.close()
