"""Create only the local fulfillment outbox; preserve existing business data."""

import argparse
import asyncio
from pathlib import Path

import asyncpg
from run_fulfillment_worker import database_url


async def main() -> None:
    parser = argparse.ArgumentParser(description="本地履约模拟 outbox 增量迁移")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    migration = (
        Path(__file__).resolve().parents[1]
        / "docker/postgres/migrations/004_fulfillment_outbox.sql"
    )
    conn = await asyncpg.connect(database_url(args.demo))
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='3s'")
            await conn.execute("SET LOCAL statement_timeout='30s'")
            await conn.execute("SELECT pg_advisory_xact_lock(7260924)")
            await conn.execute(migration.read_text())
        print("履约模拟 outbox 已就绪；原业务数据保留")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
