"""隔离测试的子进程崩溃注入器，不属于应用运行路径。"""

import argparse
import asyncio
import json
import os
from datetime import datetime
from uuid import UUID

import asyncpg

import shared.db as shared_db
from shared.after_sales import service
from shared.after_sales.operations import current_operation_id
from shared.config import settings
from shared.context import current_user_email


async def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--order", required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--now", required=True)
    parser.add_argument("--crash", choices=["before_commit", "after_commit"], required=True)
    args = parser.parse_args()
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    shared_db._pool = pool
    settings.HITL_ENABLED = False
    current_user_email.set(args.email)
    current_operation_id.set(args.operation)
    service.utc_now = lambda: datetime.fromisoformat(args.now)

    async def crash(stage: str, conn: asyncpg.Connection, operation_id: UUID) -> None:
        if stage == args.crash:
            # 退出码证明注入发生在业务写入之后。
            count = await conn.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", args.order)
            os._exit(71 if count == 1 else 79)

    service._fault_boundary = crash
    print(json.dumps(await service.request_return(args.order, "Crash test")))
    await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
