"""应用升级的增量迁移；只读取显式 DATABASE_URL，不加载私人 .env。"""

import asyncio
import os
from pathlib import Path

import asyncpg


async def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("请显式设置目标 DATABASE_URL；未连接任何数据库")
    migration_dir = Path(__file__).resolve().parents[1] / "docker/postgres/migrations"
    migrations = [
        migration_dir / name
        for name in ("002_task_state.sql", "003_task_acceptance.sql", "005_procurement_runtime.sql")
    ]
    connection = await asyncpg.connect(url)
    try:
        async with connection.transaction():
            await connection.execute("SET LOCAL lock_timeout='3s'")
            await connection.execute("SET LOCAL statement_timeout='30s'")
            await connection.execute("SELECT pg_advisory_xact_lock(7260924)")
            for migration in migrations:
                await connection.execute(migration.read_text())
        print("任务状态与记忆来源迁移完成；原数据保留")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
