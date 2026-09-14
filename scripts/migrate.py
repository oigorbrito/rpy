from __future__ import annotations

import argparse
import asyncio
import os
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


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Apply Rpy SQL migrations")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    await migrate(args.database_url)


if __name__ == "__main__":
    asyncio.run(_main())
