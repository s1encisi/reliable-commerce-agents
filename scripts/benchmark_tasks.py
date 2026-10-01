"""对本机离线固定任务做真实HTTP并发观察；不调用模型、不覆盖既有报告。"""

import argparse
import asyncio
import hashlib
import json
import math
import platform
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list[float], q: float) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * q) - 1)] if values else 0.0


async def run(base_url: str, samples: int, concurrency: list[int]) -> dict:
    if urlparse(base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("仅允许本机合成演示地址，未发送请求")
    async with httpx.AsyncClient(
        base_url=base_url, timeout=60, trust_env=False
    ) as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": "customer@example.test", "password": "DemoPass123!"},
        )
        login.raise_for_status()
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        capability = await client.get("/api/runtime/capabilities")
        capability.raise_for_status()
        if capability.json().get("offline_demo") is not True:
            raise ValueError("目标不是不录制、不启用决策模型的离线环境，未创建任务")
        goal = capability.json()["task_demo_goal"]
        groups = []
        for parallel in concurrency:
            slots = asyncio.Semaphore(parallel)
            trials = []

            async def trial(index: int):
                queued_at = time.perf_counter()
                async with slots:
                    start = time.perf_counter()
                    phases = []
                    item = {
                        "index": index,
                        "queue_ms": (start - queued_at) * 1000,
                        "phases": phases,
                    }

                    async def request(phase: str, path: str, body: dict) -> dict:
                        before = time.perf_counter()
                        response = await client.post(path, json=body)
                        phases.append(
                            {
                                "phase": phase,
                                "duration_ms": (time.perf_counter() - before) * 1000,
                                "http_status": response.status_code,
                                "server_timing": response.headers.get("server-timing"),
                                "trace_id": response.headers.get("x-trace-id"),
                            }
                        )
                        response.raise_for_status()
                        return response.json()

                    try:
                        task = await request(
                            "create",
                            "/api/tasks",
                            {
                                "goal": goal,
                                "constraints": [],
                                "requirements": {"min_items": 2},
                            },
                        )
                        item["task_id"] = task["id"]
                        task = await request(
                            "plan",
                            f"/api/tasks/{task['id']}/plan",
                            {"revision": task["revision"]},
                        )
                        if task["plan"].get("source") != "deterministic_demo":
                            raise ValueError("非固定演示计划，停止执行")
                        for _ in range(8):
                            if task["status"] != "ready":
                                break
                            task = await request(
                                "next",
                                f"/api/tasks/{task['id']}/next",
                                {"revision": task["revision"]},
                            )
                        item["status"] = task["status"]
                        item["success"] = task["status"] == "completed" and all(
                            receipt.get("verification", {}).get("passed")
                            for receipt in task["results"].values()
                        )
                        item["receipt_ids"] = [
                            value["receipt_id"] for value in task["results"].values()
                        ]
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        item["success"] = False
                        item["error_type"] = type(exc).__name__
                    item["duration_ms"] = (time.perf_counter() - start) * 1000
                    trials.append(item)

            started = time.perf_counter()
            await asyncio.gather(*(trial(i) for i in range(samples)))
            elapsed = time.perf_counter() - started
            values = [item["duration_ms"] for item in trials]
            groups.append(
                {
                    "concurrency": parallel,
                    "trials": sorted(trials, key=lambda t: t["index"]),
                    "requests": samples,
                    "succeeded": sum(t["success"] for t in trials),
                    "wall_seconds": elapsed,
                    "observed_tasks_per_second": samples / elapsed,
                    "p50_ms": percentile(values, 0.5),
                    "p95_ms_descriptive": percentile(values, 0.95),
                }
            )
    sources = [
        "scripts/benchmark_tasks.py",
        "agents/python/shared/task_state.py",
        "agents/python/shared/task_acceptance.py",
        "agents/python/orchestrator/routes/tasks.py",
        "agents/python/shared/task_observability.py",
    ]
    return {
        "scope": "本机真实HTTP固定计划与真实数据库；无LLM、无生产用户，不能外推生产容量或模型质量",
        "created_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "base_url": base_url,
        "samples_per_concurrency": samples,
        "latency_note": "duration从取得并发槽开始；客户端排队单独记录；p95为小样本描述性统计",
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True
            ).strip()
        ),
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_text().encode()).hexdigest()
            for name in sources
        },
        "groups": groups,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8180", help="已启动的本机离线演示API"
    )
    parser.add_argument(
        "--samples", type=int, default=12, help="每个并发条件的任务数量，1到100"
    )
    parser.add_argument(
        "--concurrency",
        nargs="+",
        type=int,
        default=[1, 3, 5],
        help="并发条件，每项1到10",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="新的JSON报告路径，不覆盖既有文件"
    )
    args = parser.parse_args()
    if not 1 <= args.samples <= 100 or any(not 1 <= n <= 10 for n in args.concurrency):
        parser.error("样本数量或并发超出本地演示范围")
    if args.output.exists():
        parser.error("输出已存在，请选新路径以保留原始结果")
    result = asyncio.run(run(args.base_url, args.samples, args.concurrency))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    for group in result["groups"]:
        print(
            f"并发 {group['concurrency']}：{group['succeeded']}/{group['requests']}，p50={group['p50_ms']:.2f}ms，描述性p95={group['p95_ms_descriptive']:.2f}ms"
        )
    if any(group["succeeded"] != group["requests"] for group in result["groups"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
