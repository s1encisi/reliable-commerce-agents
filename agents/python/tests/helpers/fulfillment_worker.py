"""Only tests can stop a worker at a delivery boundary; there is no HTTP fault switch."""

import argparse
import asyncio
import os

import asyncpg

from shared.after_sales import fulfillment


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--supplier-url", required=True)
    parser.add_argument("--crash", choices=["after_claim", "after_supplier_confirmation"], required=True)
    args = parser.parse_args()
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)

    async def crash(stage, event):
        if stage == args.crash:
            os._exit(73)

    fulfillment._delivery_boundary = crash
    try:
        async with fulfillment.LocalSupplier(args.supplier_url) as supplier:
            await fulfillment.dispatch_one(pool, supplier)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
