"""Apply reviewed incremental migrations without clearing existing data."""

import asyncio
from pathlib import Path

import asyncpg
from shared.config import settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "docker/postgres/migrations"


async def migrate() -> None:
    conn = await asyncpg.connect(settings.DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(87263301)")
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT NOW())"
            )
            for path in sorted(MIGRATIONS.glob("*.sql")):
                if await conn.fetchval(
                    "SELECT 1 FROM schema_migrations WHERE name = $1", path.name
                ):
                    continue
                await conn.execute(path.read_text())
                await conn.execute(
                    "INSERT INTO schema_migrations (name) VALUES ($1)", path.name
                )
                print(f"Applied {path.name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
