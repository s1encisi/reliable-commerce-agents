"""Dispatch the local simulation outbox. Explicit database only; never load the private .env."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import asyncpg
from shared.after_sales.fulfillment import LocalSupplier, dispatch_one


def database_url(demo: bool) -> str:
    if demo:
        path = Path(__file__).resolve().parents[1] / ".env.demo.local"
        if not path.exists():
            raise SystemExit("请先启动隔离演示，生成 .env.demo.local")
        values = dict(
            line.split("=", 1)
            for line in path.read_text().splitlines()
            if line and not line.startswith("#")
        )
        url = values["DATABASE_URL"]
        from urllib.parse import urlsplit

        if urlsplit(url).path != "/reliable_commerce_demo":
            raise SystemExit("--demo 只接受专用合成演示数据库")
        return url
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("请使用 --demo 或显式提供 DATABASE_URL；未连接数据库")
    return url


async def run() -> None:
    parser = argparse.ArgumentParser(
        description="本地履约模拟投递与对账，不调用支付服务"
    )
    parser.add_argument(
        "--demo", action="store_true", help="读取专用合成演示数据库地址"
    )
    parser.add_argument("--supplier-url", default="http://127.0.0.1:8199")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--reconcile",
        type=UUID,
        help="仅查询并核实指定未知/待复核记录，绝不新增供应商操作",
    )
    parser.add_argument("--poll-seconds", type=float, default=1)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds 必须大于 0")
    # Validate the local-only destination before opening a database connection.
    async with LocalSupplier(args.supplier_url) as supplier:
        pool = await asyncpg.create_pool(
            database_url(args.demo), min_size=1, max_size=2
        )
        try:
            while True:
                result = await dispatch_one(pool, supplier, reconcile_id=args.reconcile)
                if result is not None:
                    print(json.dumps(result), flush=True)
                if args.once or args.reconcile:
                    break
                await asyncio.sleep(args.poll_seconds)
        finally:
            await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
