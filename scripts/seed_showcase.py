"""容器作品展示初始化：先校验专用数据库，再迁移和幂等种子；不清空数据。"""

import asyncio
import os

import asyncpg

from scripts.migrate_db import migrate
from scripts.seed_portfolio import seed


async def main() -> None:
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    try:
        if await pool.fetchval("SELECT current_database()") != "reliable_commerce_demo":
            raise RuntimeError("展示初始化只接受专用 reliable_commerce_demo 数据库")
        await migrate()
        await seed(pool, reset=False)
    finally:
        await pool.close()
    print("展示数据库迁移与合成数据已就绪；已有记录保留。")


if __name__ == "__main__":
    asyncio.run(main())
