"""在一次性 Postgres 中可复现的退货路径对比，且不发起任何 LLM 调用。

从仓库根目录运行：
  uv run --project agents/python python -m evals.after_sales --output docs/evaluation/after-sales-results.json
冻结代码仅在子评测进程中加载。消融实验没有运行时开关。
"""

import argparse
import asyncio
import hashlib
import importlib
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
BASELINES = Path(__file__).with_name("after_sales_baselines")
NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)
CASES = [
    "normal",
    "store_credit",
    "within_window",
    "exact_boundary",
    "expired_one_second",
    "expired_old",
    "missing_delivery",
    "future_delivery",
    "conflicting_delivery",
    "not_delivered",
    "foreign_order",
    "bad_uuid",
    "empty_reason",
    "long_reason",
    "bad_refund_method",
    "same_retry",
    "parallel_same",
    "parallel_distinct",
    "payload_conflict",
    "lost_reply",
    "temporary_db",
    "persistent_db",
]
VARIANTS = ["B0", "B1", "B2", "A_no_recheck", "A_no_reconcile", "A_no_budget"]


def source_digest(path: Path) -> str:
    """Git 会归一化文本的行尾；哈希必须在 Windows/Linux 检出之间保持一致。"""
    return hashlib.sha256(path.read_text().encode("utf-8")).hexdigest()


def load_frozen(name: str, path: Path) -> None:
    parent, child = name.rsplit(".", 1)
    package = importlib.import_module(parent)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    setattr(package, child, module)
    spec.loader.exec_module(module)


def configure(variant: str) -> Any:
    if variant == "B1":
        for name in ["contracts", "policy", "approval", "service"]:
            load_frozen(f"shared.after_sales.{name}", BASELINES / f"b1_{name}.py")
    if variant in {"B0", "B1"}:
        load_frozen("shared.tools.return_tools", BASELINES / f"{variant.lower()}_return_tools.py")
    from shared.config import settings
    from shared.tools.return_tools import initiate_return

    settings.HITL_ENABLED = False
    settings.RETURN_HITL_THRESHOLD = 500
    if variant != "B0":
        from shared.after_sales import service

        service.utc_now = lambda: NOW
        if variant == "A_no_recheck":
            from shared.after_sales.contracts import Outcome, ReturnDecision

            service._submission_decision = lambda snapshot, now: ReturnDecision(
                Outcome.READY, "ABLATION", "Evaluation only"
            )
        if variant == "A_no_reconcile":

            async def unknown(operation_id: str, **kwargs: Any) -> dict[str, Any]:
                return {"success": False, "outcome": "UNKNOWN", "operation_id": operation_id}

            service.get_operation = unknown
        if variant == "A_no_budget":
            from shared.after_sales.recovery import RetryBudget

            RetryBudget.start = classmethod(lambda cls, *args, **kwargs: cls(time.monotonic() + 100, 10000))
    return initiate_return.func


def succeeded(result: dict[str, Any]) -> bool:
    return bool(result.get("return_id")) and not result.get("error") and result.get("success") is not False


class FaultPool:
    def __init__(self, pool: asyncpg.Pool, limit: int) -> None:
        self.pool, self.limit, self.hits = pool, limit, 0

    def trip(self) -> None:
        if self.hits < self.limit:
            self.hits += 1
            raise ConnectionResetError("injected database read failure")

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.trip()
        return await self.pool.execute(*args, **kwargs)

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        self.trip()
        return await self.pool.fetchrow(*args, **kwargs)

    @asynccontextmanager
    async def acquire(self, *args: Any, **kwargs: Any) -> AsyncIterator[asyncpg.Connection]:
        self.trip()
        async with self.pool.acquire(*args, **kwargs) as conn:
            yield conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self.pool, name)


async def trial(pool: asyncpg.Pool, fn: Any, variant: str, case: str, repetition: int) -> dict[str, Any]:
    import shared.db as db
    from shared.context import current_user_email

    tables = await pool.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    names = ",".join('"' + r["tablename"].replace('"', '""') + '"' for r in tables)
    await pool.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    uid = uuid5(NAMESPACE_URL, "after-sales-evaluation-user")
    oid = uuid5(NAMESPACE_URL, f"after-sales:{case}:{repetition}")
    email = "evaluation@example.test"
    await pool.execute(
        "INSERT INTO users (id, email, password_hash, name) VALUES ($1,$2,'synthetic','Evaluation')", uid, email
    )
    await pool.execute(
        "INSERT INTO orders (id,user_id,status,total,shipping_address,created_at) VALUES ($1,$2,$3,79.00,'{}',$4)",
        oid,
        uid,
        "shipped" if case == "not_delivered" else "delivered",
        NOW - timedelta(days=90),
    )
    delivered = NOW - timedelta(days=5)
    if case == "within_window":
        delivered = NOW - timedelta(days=29)
    if case == "exact_boundary":
        delivered = NOW - timedelta(days=30)
    if case == "expired_one_second":
        delivered = NOW - timedelta(days=30, seconds=1)
    if case == "expired_old":
        delivered = NOW - timedelta(days=45)
    if case == "future_delivery":
        delivered = NOW + timedelta(days=1)
    if case != "missing_delivery":
        await pool.execute(
            "INSERT INTO order_status_history(order_id,status,timestamp) VALUES ($1,'delivered',$2)", oid, delivered
        )
    if case == "conflicting_delivery":
        await pool.execute(
            "INSERT INTO order_status_history(order_id,status,timestamp) VALUES ($1,'delivered',$2)",
            oid,
            delivered - timedelta(days=1),
        )
    current_user_email.set("other@example.test" if case == "foreign_order" else email)
    reason = " " if case == "empty_reason" else "x" * 300 if case == "long_reason" else "Synthetic return reason"
    method = (
        "invalid" if case == "bad_refund_method" else "store_credit" if case == "store_credit" else "original_payment"
    )
    args = {"order_id": "invalid" if case == "bad_uuid" else str(oid), "reason": reason, "refund_method": method}
    operation_token = None
    if variant not in {"B0", "B1"}:
        from shared.after_sales.operations import current_operation_id

        operation_token = current_operation_id.set(str(uuid5(NAMESPACE_URL, f"operation:{case}:{repetition}")))
    fault_hits = 0
    restore = None
    faulty = None
    db._pool = pool
    if case in {"temporary_db", "persistent_db"}:
        faulty = FaultPool(pool, 2 if case == "temporary_db" else 10000)
        db._pool = faulty
    if case == "lost_reply":
        if variant in {"B0", "B1"}:
            import shared.idempotency as idem

            original = idem._complete

            async def drop_cache(*a: Any, **kw: Any) -> None:
                nonlocal fault_hits
                if not fault_hits:
                    fault_hits += 1
                    raise ConnectionResetError("injected after business commit, before result cache")
                await original(*a, **kw)

            idem._complete = drop_cache

            def restore() -> None:
                setattr(idem, "_complete", original)
        else:
            from shared.after_sales import service

            original = service._fault_boundary

            async def drop_response(stage: str, conn: asyncpg.Connection, operation_id: Any) -> None:
                nonlocal fault_hits
                if stage == "after_commit" and not fault_hits:
                    fault_hits += 1
                    raise ConnectionResetError("injected after commit, before response")

            service._fault_boundary = drop_response

            def restore() -> None:
                setattr(service, "_fault_boundary", original)

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    try:
        async with asyncio.timeout(1 if case == "persistent_db" else 10):
            if case.startswith("parallel_"):
                if case == "parallel_distinct" and operation_token is not None:
                    current_operation_id.set(None)
                results = await asyncio.gather(
                    *(
                        fn(**{**args, "reason": f"reason {n}" if case == "parallel_distinct" else reason})
                        for n in range(3)
                    )
                )
            else:
                try:
                    results = [await fn(**args)]
                except ConnectionResetError:
                    if case != "lost_reply":
                        raise
                    results = [await fn(**args)]  # 响应丢失后显式重试一次
                if case in {"same_retry", "payload_conflict"}:
                    results.append(await fn(**{**args, "reason": "changed" if case == "payload_conflict" else reason}))
    except TimeoutError:
        results = [{"outcome": "TIMEOUT", "success": False}]
    except Exception as exc:
        results = [{"outcome": "EXCEPTION", "exception_type": type(exc).__name__, "success": False}]
    finally:
        if restore:
            restore()
        db._pool = pool
        if operation_token is not None:
            current_operation_id.reset(operation_token)
    duration = (time.perf_counter() - started) * 1000
    count = await pool.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", oid)
    deny = case in {
        "expired_one_second",
        "expired_old",
        "missing_delivery",
        "future_delivery",
        "conflicting_delivery",
        "not_delivered",
        "foreign_order",
        "bad_uuid",
        "empty_reason",
        "long_reason",
        "bad_refund_method",
    }
    if deny:
        passed = count == 0 and all(not succeeded(r) and r.get("outcome") != "EXCEPTION" for r in results)
    elif case == "persistent_db":
        passed = count == 0 and results[0].get("outcome") == "RETRYABLE_FAILURE" and faulty.hits <= 3
    elif case == "payload_conflict":
        passed = count == 1 and results[-1].get("error_code") == "OPERATION_CONFLICT"
    elif case == "parallel_distinct":
        passed = count == 1 and sum(succeeded(r) for r in results) == 1
    else:
        passed = count == 1 and all(succeeded(r) for r in results)
    observed = faulty.hits if faulty else fault_hits
    fault_requested = case in {"lost_reply", "temporary_db", "persistent_db"}
    if fault_requested and observed == 0:
        passed = False  # 故障注入失败绝不会被算作成功恢复
    return {
        "case_id": case,
        "repetition": repetition,
        "passed": passed,
        "database_returns": count,
        "illegal_write": bool(deny and count),
        "duplicate_effect": count > 1,
        "outcomes": [r.get("outcome", "SUCCEEDED" if succeeded(r) else "REJECTED") for r in results],
        "fault_requested": fault_requested,
        "fault_boundary": "after business commit"
        if case == "lost_reply"
        else "before database preparation"
        if faulty
        else None,
        "fault_observed": observed,
        "duration_ms": round(duration, 3),
    }


async def child(variant: str, repetitions: int) -> dict[str, Any]:
    fn = configure(variant)
    pool = await asyncpg.create_pool(
        os.environ["AFTER_SALES_EVAL_DSN"],
        min_size=1,
        max_size=8,
        server_settings={"search_path": "public,pg_catalog", "timezone": "UTC"},
    )
    try:
        assert await pool.fetchval("SELECT current_database()") == "isolated_after_sales_eval"
        assert await pool.fetchval("SELECT now()") == NOW
        rows = [await trial(pool, fn, variant, case, repetition) for repetition in range(repetitions) for case in CASES]
        times = sorted(r["duration_ms"] for r in rows)
        return {
            "variant": variant,
            "trials": len(rows),
            "passed": sum(r["passed"] for r in rows),
            "illegal_writes": sum(r["illegal_write"] for r in rows),
            "duplicate_effects": sum(r["duplicate_effect"] for r in rows),
            "p50_ms": statistics.median(times),
            "p95_ms_exploratory": times[int((len(times) - 1) * 0.95)],
            "cases": rows,
        }
    finally:
        await pool.close()


async def load_checks() -> list[dict[str, Any]]:
    """观测到的本地服务负载，并非关于生产容量的论断。"""
    configure("B2")
    import shared.db as db
    from shared.after_sales import service
    from shared.context import current_user_email

    pool = await asyncpg.create_pool(os.environ["AFTER_SALES_EVAL_DSN"], min_size=1, max_size=8)
    try:
        assert await pool.fetchval("SELECT current_database()") == "isolated_after_sales_eval"
        db._pool = pool
        uid = uuid5(NAMESPACE_URL, "load-user")
        await pool.execute(
            "INSERT INTO users(id,email,password_hash,name) VALUES($1,'load@example.test','synthetic','Load')", uid
        )
        current_user_email.set("load@example.test")
        rows = []
        for concurrency in [1, 3, 5]:
            orders = [uuid5(NAMESPACE_URL, f"load:{concurrency}:{i}") for i in range(100)]
            await pool.executemany(
                "INSERT INTO orders(id,user_id,status,total,shipping_address,created_at) "
                "VALUES($1,$2,'delivered',10.00,'{}',$3)",
                [(oid, uid, NOW - timedelta(days=10)) for oid in orders],
            )
            await pool.executemany(
                "INSERT INTO order_status_history(order_id,status,timestamp) VALUES($1,'delivered',$2)",
                [(oid, NOW - timedelta(days=5)) for oid in orders],
            )
            semaphore = asyncio.Semaphore(concurrency)

            async def one(oid: Any) -> tuple[bool, float]:
                async with semaphore:
                    start = time.perf_counter()
                    result = await service.request_return(str(oid), "Synthetic load sample")
                    return succeeded(result), (time.perf_counter() - start) * 1000

            started = time.perf_counter()
            observations = await asyncio.gather(*(one(oid) for oid in orders))
            elapsed = time.perf_counter() - started
            times = sorted(x[1] for x in observations)
            rows.append(
                {
                    "concurrency": concurrency,
                    "requests": 100,
                    "successful": sum(x[0] for x in observations),
                    "elapsed_seconds": round(elapsed, 4),
                    "throughput_per_second": round(100 / elapsed, 2),
                    "service_p50_ms": round(statistics.median(times), 3),
                    "service_p95_ms": round(times[94], 3),
                }
            )
        return rows
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".local/after-sales-results.json")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--child", choices=VARIANTS)
    parser.add_argument("--load-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.load_child:
        print(json.dumps(asyncio.run(load_checks())))
        return
    if args.child:
        print(json.dumps(asyncio.run(child(args.child, args.repetitions))))
        return
    from testcontainers.community.postgres import PostgresContainer

    source = sorted(
        {
            *(ROOT / "agents/python/shared/after_sales").glob("*.py"),
            Path(__file__).resolve(),
            ROOT / "agents/python/shared/tools/return_tools.py",
            ROOT / "agents/python/shared/hitl.py",
            ROOT / "agents/python/shared/idempotency.py",
            ROOT / "agents/python/uv.lock",
            ROOT / "docker/postgres/init.sql",
        }
    )
    frozen_hashes = {str(p.relative_to(ROOT)): source_digest(p) for p in source}
    baseline_hashes = json.loads((BASELINES / "manifest.json").read_text())
    for name, digest in baseline_hashes.items():
        if source_digest(BASELINES / name) != digest:
            raise RuntimeError(f"Frozen baseline changed: {name}")

    with PostgresContainer("pgvector/pgvector:pg16", dbname="isolated_after_sales_eval") as postgres:
        dsn = postgres.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")

        async def initialize() -> None:
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute((ROOT / "docker/postgres/init.sql").read_text())
                await conn.execute(
                    "CREATE FUNCTION public.now() RETURNS timestamptz LANGUAGE sql AS $$ SELE"
                    "CT '2026-09-16T12:00:00Z'::timestamptz $$"
                )
            finally:
                await conn.close()

        asyncio.run(initialize())
        variants = []
        for variant in VARIANTS:
            env = {
                **os.environ,
                "AFTER_SALES_EVAL_DSN": dsn,
                "OPENAI_API_KEY": "",
                "AZURE_OPENAI_KEY": "",
                "AZURE_OPENAI_API_KEY": "",
            }
            result = subprocess.run(
                [sys.executable, "-m", "evals.after_sales", "--child", variant, "--repetitions", str(args.repetitions)],
                env=env,
                capture_output=True,
                text=True,
                check=True,
                timeout=240,
            )
            variants.append(json.loads(result.stdout))
            print(f"{variant}: {variants[-1]['passed']}/{variants[-1]['trials']} criteria passed", flush=True)
        load_result = subprocess.run(
            [sys.executable, "-m", "evals.after_sales", "--load-child"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        if frozen_hashes != {str(p.relative_to(ROOT)): source_digest(p) for p in source}:
            raise RuntimeError("Implementation changed during evaluation; discard this run")
        report = {
            "scope": "Return tool/service microbenchmark; no LLM, no production traffic",
            "fixed_clock": NOW.isoformat(),
            "source_hash_format": "SHA-256 of UTF-8 source with LF-normalized newlines",
            "repetitions_are_independent_samples": False,
            "latency_note": "Local synthetic trials; p95 is exploratory, not capacity or an SLA",
            "baseline_manifest": baseline_hashes,
            "implementation_sha256": frozen_hashes,
            "variants": variants,
            "local_service_load": json.loads(load_result.stdout),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
