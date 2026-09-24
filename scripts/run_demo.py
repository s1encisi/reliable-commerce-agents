"""启动独立本地演示，不读取真实模型凭据，也不重置主环境。"""

import argparse
import asyncio
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def demo_environment() -> dict[str, str]:
    path = ROOT / ".env.demo.local"
    if not path.exists():
        password = secrets.token_hex(16)
        entries = {
            "DEMO_DB_PASSWORD": password,
            "DATABASE_URL": f"postgresql://demo:{password}@localhost:55432/reliable_commerce_demo",
            "JWT_SECRET": secrets.token_hex(32),
            "AGENT_SHARED_SECRET": secrets.token_hex(32),
        }
        path.write_text("\n".join(f"{k}={v}" for k, v in entries.items()) + "\n")
    path.chmod(0o600)
    # 这是生成的数据文件，不是 Shell 脚本，禁止 source 或 eval。
    values = dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )
    env = {
        **os.environ,
        **values,
        "REDIS_URL": "redis://localhost:56379",
        "LLM_PROVIDER": "replay",
        "RECORD": "false",
        "OPENAI_API_KEY": "",
        "AZURE_OPENAI_KEY": "",
        "AZURE_OPENAI_API_KEY": "",
        "AUTH_MODE": "local",
        "OTEL_ENABLED": "false",
        "HITL_ENABLED": "true",
        "MAF_CHECKPOINT_BACKEND": "postgres",
        "ORCHESTRATOR_URL": "http://localhost:8180",
        "ORCHESTRATION_MODE": "workflow:return-replace",
        "NEXT_TELEMETRY_DISABLED": "1",
        "PYTHONPATH": str(ROOT / "agents/python") + os.pathsep + str(ROOT),
    }
    names = [
        "product-discovery",
        "order-management",
        "pricing-promotions",
        "review-sentiment",
        "inventory-fulfillment",
    ]
    env["AGENT_REGISTRY"] = json.dumps(
        {name: f"http://localhost:{8181 + i}" for i, name in enumerate(names)}
    )
    env["NO_PROXY"] = env.get("NO_PROXY", "") + ",localhost,127.0.0.1,::1"
    return env


async def initialize(reset: bool) -> None:
    import asyncpg
    from scripts.seed_portfolio import seed

    deadline = time.monotonic() + 45
    while True:
        try:
            pool = await asyncpg.create_pool(
                os.environ["DATABASE_URL"], min_size=1, max_size=3
            )
            break
        except (OSError, asyncpg.PostgresError):
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(1)
    try:
        await seed(pool, reset=reset)
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset", action="store_true", help="Reset ONLY synthetic portfolio demo data"
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Prepare demo data without starting app processes",
    )
    args = parser.parse_args()
    env = demo_environment()
    os.environ.update(env)
    subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(ROOT / ".env.demo.local"),
            "-f",
            "docker-compose.portfolio.yml",
            "up",
            "-d",
            "--pull",
            "never",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )
    asyncio.run(initialize(args.reset))
    subprocess.run(
        [os.sys.executable, "scripts/migrate_db.py"], cwd=ROOT, env=env, check=True
    )
    if args.seed_only:
        print("Synthetic portfolio data is ready.")
        return
    logs = ROOT / ".local/demo"
    logs.mkdir(parents=True, exist_ok=True)
    processes: list[subprocess.Popen] = []
    handles = []
    try:
        agents = [
            "orchestrator",
            "product_discovery",
            "order_management",
            "pricing_promotions",
            "review_sentiment",
            "inventory_fulfillment",
        ]
        for i, name in enumerate(agents):
            handle = (logs / f"{name}.log").open("w")
            handles.append(handle)
            processes.append(
                subprocess.Popen(
                    [
                        os.sys.executable,
                        "-m",
                        "uvicorn",
                        f"{name}.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(8180 + i),
                    ],
                    cwd=ROOT / "agents/python",
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            )
        handle = (logs / "web.log").open("w")
        handles.append(handle)
        processes.append(
            subprocess.Popen(
                [
                    "pnpm",
                    "--dir",
                    "web",
                    "dev",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    "3010",
                ],
                cwd=ROOT,
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        )
        print("演示地址: http://localhost:3010  | API: http://localhost:8180", flush=True)
        print(
            "customer@example.test / DemoPass123!  | admin@example.test / DemoPass123!",
            flush=True,
        )
        print(
            "未接入真实大模型：请使用「退货与换货」工作流，或访问订单 / 审批页面。Ctrl-C 可停止应用进程。",
            flush=True,
        )
        while all(process.poll() is None for process in processes):
            time.sleep(1)
        raise RuntimeError("有演示进程退出；请查看 .local/demo/*.log")
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    main()
