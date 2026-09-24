#!/usr/bin/env python3
"""对五种编排模式进行基准测试，针对一个正在运行的技术栈。

站点上写着同一个问题可以经由五种路径被路由并加以比较。这里就生成这种比较，
并附上具体数字。

**它驱动的是 HTTP API，而不是直接调用各模式。** 在请求体中带 `mode` 的
`POST /api/chat` 正是编辑器里模式切换器所发送的内容，因此在这里跑一次
就是走真实路径——鉴权、护栏、净化、事实核验（grounding）、人工参与
（Human-in-the-Loop，HITL）闸门、用量记录——而不是它的副本。在进程内调用
`get_mode(...).run()` 代码更少，但测量的却是没人会真正运行的东西。

Token 以每次请求前后的差值形式从 `usage_logs` 读取，成本则来自
`shared.cost.estimate_cost`。当某个模式没有记录任何用量行时，会报告为
"not captured" 而不是零，因为这两者是截然不同的结论。

这会花费真金白银，且无法在 `LLM_PROVIDER=replay` 下运行——回放夹具会瞬时
返回，从而使延迟数据失去意义。它被刻意排除在 CI 之外；PR 闸门仍然使用
免费的、由回放驱动的冒烟测试套件。

用法::

    # 在技术栈已运行的情况下（./scripts/dev.sh --demo）
    uv run python -m evals.benchmark_modes --reps 3
    uv run python -m evals.benchmark_modes --modes tool,handoff --reps 1 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import httpx

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS_DIR = Path(__file__).resolve().parent / "results"

ORCH_URL = os.environ.get("ORCH_URL", "http://localhost:8080")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents")
DEMO_USER = os.environ.get("BENCH_USER", "alice.johnson@gmail.com")
DEMO_PASS = os.environ.get("BENCH_PASS", "customer123")

ALL_MODES = ["tool", "handoff", "workflow:pre-purchase", "workflow:return-replace", "group-chat"]

# 提示词的选取保证每种模式都有正当的事情可做。两个工作流模式会自行从消息中
# 解析出 product_id / order_id，因此一套只用于路由的提示词集合会让它们显得
# 人为地糟糕——它们会把时间花在解析不出实体上，而不是花在编排上。
PROMPTS: list[dict[str, str]] = [
    {"id": "product-search", "text": "What Allbirds products do you have and what do they cost?"},
    {"id": "order-status", "text": "What is the status of my most recent order?"},
    {"id": "pre-purchase", "text": "I'm thinking about the Allbirds Wool Runners — should I buy them?"},
    {"id": "return", "text": "I want to return my most recent order because it does not fit."},
]


@dataclass
class RunResult:
    mode: str
    prompt_id: str
    rep: int
    ok: bool
    latency_ms: float
    response_chars: int = 0
    agents_involved: list[str] = field(default_factory=list)
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    error: str = ""


async def login(client: httpx.AsyncClient) -> str:
    resp = await client.post(f"{ORCH_URL}/api/auth/login", json={"email": DEMO_USER, "password": DEMO_PASS}, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    token = body.get("access_token") or body.get("token")
    if not token:
        raise SystemExit(f"login succeeded but no token in response: {sorted(body)}")
    return token


async def usage_totals(pool: asyncpg.Pool) -> tuple[int, int]:
    row = await pool.fetchrow(
        "SELECT COALESCE(SUM(tokens_in), 0) AS ti, COALESCE(SUM(tokens_out), 0) AS to_ FROM usage_logs"
    )
    return int(row["ti"]), int(row["to_"])


async def run_once(
    client: httpx.AsyncClient, pool: asyncpg.Pool, token: str, mode: str, prompt: dict[str, str], rep: int
) -> RunResult:
    before_in, before_out = await usage_totals(pool)
    started = time.perf_counter()
    try:
        resp = await client.post(
            f"{ORCH_URL}/api/chat",
            json={"message": prompt["text"], "mode": mode},
            headers={"Authorization": f"Bearer {token}"},
            timeout=300,
        )
        elapsed = (time.perf_counter() - started) * 1000
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - 某个模式失败是一个数据点，而不是崩溃
        return RunResult(
            mode=mode,
            prompt_id=prompt["id"],
            rep=rep,
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )

    after_in, after_out = await usage_totals(pool)
    delta_in, delta_out = after_in - before_in, after_out - before_out

    cost = None
    if delta_in or delta_out:
        from shared.cost import estimate_cost

        cost = estimate_cost(os.environ.get("LLM_MODEL", "gpt-4.1"), delta_in, delta_out)

    return RunResult(
        mode=mode,
        prompt_id=prompt["id"],
        rep=rep,
        ok=True,
        latency_ms=elapsed,
        response_chars=len(body.get("response", "")),
        agents_involved=body.get("agents_involved") or [],
        tokens_in=delta_in if (delta_in or delta_out) else None,
        tokens_out=delta_out if (delta_in or delta_out) else None,
        cost_usd=cost,
    )


def summarise(results: list[RunResult]) -> list[dict[str, object]]:
    rows = []
    for mode in dict.fromkeys(r.mode for r in results):
        runs = [r for r in results if r.mode == mode]
        ok = [r for r in runs if r.ok]
        lat = sorted(r.latency_ms for r in ok)
        toks = [(r.tokens_in or 0) + (r.tokens_out or 0) for r in ok if r.tokens_in is not None]
        costs = [r.cost_usd for r in ok if r.cost_usd is not None]
        agents = sorted({a for r in ok for a in r.agents_involved})
        rows.append(
            {
                "mode": mode,
                "runs": len(runs),
                "ok": len(ok),
                "p50_ms": round(statistics.median(lat)) if lat else None,
                "p95_ms": round(lat[int(len(lat) * 0.95)] if len(lat) > 1 else lat[0]) if lat else None,
                "mean_tokens": round(statistics.mean(toks)) if toks else None,
                "mean_cost_usd": round(statistics.mean(costs), 5) if costs else None,
                "rate_limited": sum(1 for r in runs if not r.ok and "429" in r.error),
                "agents_seen": agents,
            }
        )
    return rows


def markdown(summary: list[dict[str, object]], meta: dict[str, object]) -> str:
    def cell(value: object) -> str:
        return "not captured" if value is None else str(value)

    lines = [
        "| Mode | Runs | OK | p50 latency | p95 latency | Mean tokens | Mean cost | Agents involved |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in summary:
        lines.append(
            f"| `{row['mode']}` | {row['runs']} | {row['ok']} | "
            f"{cell(row['p50_ms'])} ms | {cell(row['p95_ms'])} ms | "
            f"{cell(row['mean_tokens'])} | "
            f"{'$' + format(row['mean_cost_usd'], '.5f') if row['mean_cost_usd'] is not None else 'not captured'} | "
            f"{', '.join(row['agents_seen']) or '—'} |"
        )
    lines += [
        "",
        f"Model `{meta['model']}` · {meta['reps']} repetition(s) × {meta['prompts']} prompts · "
        f"measured {meta['timestamp']} · commit `{meta['commit']}`",
    ]
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    modes = [m.strip() for m in args.modes.split(",")] if args.modes else ALL_MODES
    unknown = [m for m in modes if m not in ALL_MODES]
    if unknown:
        raise SystemExit(f"unknown mode(s): {unknown}. Available: {ALL_MODES}")

    total = len(modes) * len(PROMPTS) * args.reps
    print(f"{len(modes)} modes × {len(PROMPTS)} prompts × {args.reps} rep(s) = {total} real LLM calls")
    if args.dry_run:
        for mode in modes:
            for prompt in PROMPTS:
                print(f"  would run [{mode}] {prompt['id']}")
        return 0

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    assert pool is not None
    results: list[RunResult] = []
    async with httpx.AsyncClient() as client:
        token = await login(client)
        print(f"logged in as {DEMO_USER}\n")
        for mode in modes:
            for prompt in PROMPTS:
                for rep in range(1, args.reps + 1):
                    if results:
                        await asyncio.sleep(args.delay)
                    res = await run_once(client, pool, token, mode, prompt, rep)
                    results.append(res)
                    mark = "ok " if res.ok else "ERR"
                    print(f"  [{mark}] {mode:24} {prompt['id']:14} rep{rep} {res.latency_ms:7.0f}ms {res.error}")
    await pool.close()

    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        commit = "unknown"

    meta = {
        "model": os.environ.get("LLM_MODEL", "gpt-4.1"),
        "provider": os.environ.get("LLM_PROVIDER", "openai"),
        "reps": args.reps,
        "prompts": len(PROMPTS),
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": commit,
    }
    summary = summarise(results)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = meta["timestamp"].replace(":", "").replace("-", "")[:15]
    out_json = RESULTS_DIR / f"mode-benchmark-{stamp}.json"
    out_json.write_text(
        json.dumps({"meta": meta, "summary": summary, "runs": [asdict(r) for r in results]}, indent=2),
        encoding="utf-8",
    )

    table = markdown(summary, meta)
    print("\n" + table + f"\n\nwrote {out_json.relative_to(REPO_ROOT)}")
    (RESULTS_DIR / "latest.md").write_text(table + "\n", encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reps", type=int, default=3, help="Repetitions per mode/prompt (default: 3)")
    parser.add_argument("--modes", help=f"Comma-separated subset of: {','.join(ALL_MODES)}")
    parser.add_argument("--dry-run", action="store_true", help="List what would run; spend nothing")
    parser.add_argument(
        "--delay",
        type=float,
        default=8.0,
        help="Seconds to wait between requests (default: 8). The chat routes are behind a Redis "
        "sliding-window limiter keyed by user; firing back-to-back trips it and the run then "
        "measures 429s instead of orchestration.",
    )
    args = parser.parse_args()

    if os.environ.get("LLM_PROVIDER") == "replay" and not args.dry_run:
        raise SystemExit("LLM_PROVIDER=replay returns instantly — latency would be meaningless. Refusing.")

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
