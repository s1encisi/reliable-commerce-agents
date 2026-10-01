"""追加一笔录制用合成订单；仅允许既有 reliable_commerce_demo，不清空数据。"""

import argparse
import asyncio
import json
import os

import asyncpg


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="使用宿主隔离演示的 .env.demo.local 数据库地址")
    parser.add_argument("--scenario", choices=["normal", "missing_delivery", "expired"], default="normal")
    args = parser.parse_args()
    # 在导入项目设置之前禁止读取私人环境文件，也不允许触发模型录制。
    os.environ["APP_ENV_FILE"] = "/dev/null"
    os.environ["RECORD"] = "false"
    for key in (
        "OPENAI_API_KEY",
        "AZURE_OPENAI_KEY",
        "AZURE_OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "TYPESAFE_API_KEY",
    ):
        os.environ[key] = ""

    from scripts.run_fulfillment_worker import database_url
    from scripts.seed_portfolio import prepare_recording_order

    pool = await asyncpg.create_pool(database_url(args.demo), min_size=1, max_size=1)
    try:
        result = await prepare_recording_order(pool, scenario=args.scenario)
    finally:
        await pool.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
