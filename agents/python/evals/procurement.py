"""采购工程评测：独立合成 PostgreSQL，确定性工具，禁止借用真实模型质量结论。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import platform
import statistics
import subprocess
import time
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[3]
DATASET = Path(__file__).with_name("procurement_data") / "tasks.json"
OWNER = "procurement-eval@example.test"
VARIANTS = ("sequential_adapter", "maf", "no_parallel", "no_local_repair")
DB_NAME = "procurement_eval"


def entity_id(key: str):
    return uuid5(NAMESPACE_URL, f"reliable-commerce:procurement-eval:v1:{key}")


def load_dataset(path: Path = DATASET) -> dict:
    from shared.procurement.models import ProcurementSpec

    dataset = json.loads(path.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    if len(cases) != 30 or len({case["id"] for case in cases}) != 30:
        raise ValueError("必须保留全部 30 个独立场景")
    if sum(case["split"] == "dev" for case in cases) != 18 or sum(case["split"] == "holdout" for case in cases) != 12:
        raise ValueError("必须保持 18 dev / 12 holdout")
    for case in cases:
        ProcurementSpec.model_validate(case["spec"])
        keys = {product["key"] for product in case["catalog"]}
        if not set(case["expected"]["acceptable_product_keys"]) <= keys:
            raise ValueError("期望商品不在此场景目录中")
        for event in case["events"]:
            if event["at"] not in {"after_discovery", "after_capability", "before_finalize"}:
                raise ValueError("故障只能按固定逻辑事件触发")
            if event.get("product") and event["product"] not in keys:
                raise ValueError("故障商品不属于场景")
    return dataset


def source_manifest() -> dict:
    paths = [
        *sorted((ROOT / "agents/python/shared/procurement").glob("*.py")),
        Path(__file__),
        DATASET,
        ROOT / "agents/python/shared/task_state.py",
        ROOT / "agents/python/shared/search.py",
        ROOT / "scripts/seed_procurement.py",
        ROOT / "agents/python/uv.lock",
        ROOT / "docker/postgres/init.sql",
    ]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


async def seed_case(pool, case: dict) -> dict:
    """只在CLI自身创建的专用容器内使用；无任意DSN参数或主库重置路径。"""
    async with pool.acquire() as conn, conn.transaction():
        if await conn.fetchval("SELECT current_database()") != DB_NAME:
            raise RuntimeError("评测种子拒绝非采购隔离库")
        await conn.execute("TRUNCATE agent_tasks,users,coupons,products,warehouses CASCADE")
        owner = entity_id("owner")
        await conn.execute(
            "INSERT INTO users(id,email,password_hash,name) VALUES($1,$2,'unused','隔离采购评测用户')", owner, OWNER
        )
        warehouse = entity_id("warehouse")
        await conn.execute(
            "INSERT INTO warehouses(id,name,location,region) VALUES($1,'合成仓','隔离容器','east')", warehouse
        )
        ids = {}
        for product in case["catalog"]:
            pid = entity_id(case["id"] + ":" + product["key"])
            ids[product["key"]] = str(pid)
            await conn.execute(
                """INSERT INTO products(id,name,description,category,price,rating,review_count)
                   VALUES($1,$2,'仅用于独立合成采购工程评测',$3,$4,$5,$6)""",
                pid,
                product["name"],
                product["category"],
                Decimal(product["price"]),
                Decimal(product["rating"]),
                len(product["reviews"]),
            )
            await conn.execute(
                "INSERT INTO warehouse_inventory(warehouse_id,product_id,quantity) VALUES($1,$2,$3)",
                warehouse,
                pid,
                product["stock"],
            )
            for index, review in enumerate(product["reviews"]):
                await conn.execute(
                    """INSERT INTO reviews(id,product_id,user_id,rating,title,body,verified_purchase)
                       VALUES($1,$2,$3,$4,$5,$6,true)""",
                    entity_id(f"{case['id']}:{product['key']}:review:{index}"),
                    pid,
                    owner,
                    review["rating"],
                    review["title"],
                    review["body"],
                )
        now = datetime.now(UTC)
        for coupon in case["coupons"]:
            cid = entity_id(case["id"] + ":" + coupon["key"])
            ids[coupon["key"]] = str(cid)
            await conn.execute(
                """INSERT INTO coupons(id,code,discount_type,discount_value,min_spend,max_discount,
                                       usage_limit,times_used,valid_from,valid_until,applicable_categories,
                                       user_specific_email,is_active)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
                cid,
                str(cid),
                coupon["discount_type"],
                Decimal(coupon["discount_value"]),
                Decimal(coupon["min_spend"]),
                Decimal(coupon["max_discount"]) if coupon["max_discount"] is not None else None,
                coupon["usage_limit"],
                coupon["times_used"],
                now + timedelta(seconds=coupon["valid_from_seconds"]),
                now + timedelta(seconds=coupon["valid_until_seconds"]),
                coupon["applicable_categories"],
                OWNER if coupon["owner"] == "self" else "other@example.test",
                coupon["is_active"],
            )
    return ids


class LogicalEvents:
    def __init__(self, pool, case: dict, ids: dict):
        self.pool, self.case, self.ids = pool, case, ids
        self.seen = {}
        self.fired = []
        self.timeline = []

    async def __call__(self, name: str, task: dict, payload: dict):
        step = payload.get("step")
        step = step.model_dump() if hasattr(step, "model_dump") else step or {}
        outcome = payload.get("outcome")
        outcome = outcome.model_dump() if hasattr(outcome, "model_dump") else outcome or {}
        data = outcome.get("data", {})
        pids = {item["product_id"] for item in data.get("items", [])}
        self.timeline.append(
            {
                "event": name,
                "step_id": step.get("id"),
                "capability": step.get("capability"),
                "product_ids": sorted(pids),
                "reused": payload.get("reused", False),
            }
        )
        for index, event in enumerate(self.case["events"]):
            if name != event["at"] or event.get("capability") not in (None, step.get("capability")):
                continue
            if name != "before_finalize" and event.get("product") and self.ids[event["product"]] not in pids:
                continue
            self.seen[index] = self.seen.get(index, 0) + 1
            if self.seen[index] != event["occurrence"] or index in self.fired:
                continue
            pid = self.ids.get(event.get("product"))
            if event["action"] == "price":
                await self.pool.execute("UPDATE products SET price=$2 WHERE id=$1", pid, Decimal(event["value"]))
            elif event["action"] == "stock":
                await self.pool.execute(
                    "UPDATE warehouse_inventory SET quantity=$2 WHERE product_id=$1", pid, event["value"]
                )
            elif event["action"] == "delete_reviews":
                await self.pool.execute("DELETE FROM reviews WHERE product_id=$1", pid)
            elif event["action"] == "expire_coupon":
                await self.pool.execute(
                    "UPDATE coupons SET valid_until=clock_timestamp()-INTERVAL '1 second' WHERE id=$1",
                    self.ids[event["coupon"]],
                )
            else:
                raise ValueError("未知逻辑事件")
            self.fired.append(index)


async def reference_check(pool, case: dict, ids: dict, options: list) -> dict:
    """独立评分：当前原始SQL事实+数据集可接受集合，不依赖运行器的passed字段。"""
    issues = []
    if len(options) != case["spec"]["alternative_count"]:
        issues.append("option_count")
    if len({option.get("product_id") for option in options}) != len(options):
        issues.append("duplicate_product")
    allowed = {ids[key] for key in case["expected"]["acceptable_product_keys"]}
    qty = case["spec"]["quantity"]
    budget = Decimal(case["spec"]["total_budget"])
    now = datetime.now(UTC)
    for option in options:
        pid = option.get("product_id")
        if pid not in allowed:
            issues.append("unexpected_product")
            continue
        product = await pool.fetchrow("SELECT * FROM products WHERE id=$1", pid)
        available = await pool.fetchval(
            "SELECT coalesce(sum(quantity),0) FROM warehouse_inventory WHERE product_id=$1", pid
        )
        if not product or not product["is_active"]:
            issues.append("unknown_or_inactive_product")
            continue
        if option.get("quantity") != qty or available < qty:
            issues.append("quantity_or_stock")
        if case["spec"]["min_rating"] is not None and product["rating"] < Decimal(str(case["spec"]["min_rating"])):
            issues.append("rating")
        original = product["price"] * qty
        discounts = [Decimal(0)]
        for coupon in await pool.fetch("SELECT * FROM coupons"):
            if (
                not coupon["is_active"]
                or coupon["user_specific_email"] not in (None, OWNER)
                or coupon["applicable_categories"] is not None
                and product["category"] not in coupon["applicable_categories"]
                or coupon["valid_from"]
                and now < coupon["valid_from"]
                or coupon["valid_until"]
                and now >= coupon["valid_until"]
                or coupon["usage_limit"] is not None
                and coupon["times_used"] >= coupon["usage_limit"]
                or original < coupon["min_spend"]
            ):
                continue
            discount = coupon["discount_value"]
            if coupon["discount_type"] == "percentage":
                discount = (original * discount / 100).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
            if coupon["max_discount"] is not None:
                discount = min(discount, coupon["max_discount"])
            discounts.append(min(original, discount))
        final = original - max(discounts)
        quote = option.get("quote", {})
        try:
            if Decimal(quote.get("final_total", "-1")) != final or final > budget:
                issues.append("quote_or_budget")
        except Exception:
            issues.append("malformed_quote")
        originals = {
            str(row["id"]): row["body"]
            for row in await pool.fetch("SELECT id,body FROM reviews WHERE product_id=$1", pid)
        }
        reviews = option.get("review", {}).get("reviews", [])
        if not originals or not reviews:
            issues.append("insufficient_reviews")
        for review in reviews:
            if originals.get(review.get("id")) != review.get("body"):
                issues.append("review_not_original")
    return {"passed": not issues, "issues": issues}


def coarse_reuse_key(task, step, dependencies) -> str:
    """原顺序策略采购适配：完整条件+直接依赖回执变化使下游失效。"""
    from shared.procurement.models import canonical_hash

    return canonical_hash(
        {
            "requirements": task["requirements"],
            "definition": step.model_dump(mode="json"),
            "dependencies": {key: value.model_dump(mode="json") for key, value in dependencies.items()},
        }
    )


def outcome_failures(outcomes: dict) -> list[dict]:
    failures = []
    for step_id, outcome in outcomes.items():
        if outcome.status == "succeeded":
            continue
        data = outcome.data
        failures.append(
            {
                "step_id": step_id,
                "code": outcome.error_code,
                "reason": outcome.message,
                "capabilities": [data["capability"]] if data.get("capability") else [],
                "affected_product_ids": data.get("affected_product_ids", []),
                "data": data,
            }
        )
    return failures


async def sequential_adapter(pool, task_id, dispatcher, finalizer, event_hook):
    """旧 ready[0] 推进思路的采购适配器，复用相同PG回执与一次修复约束。

    不是原query API的采购能力测试；对象映射变化沿整个直接依赖回执失效，
    不使用新运行器按SKU/能力投影的细粒度复用。已有成功回执仍受TTL约束。
    """
    from shared.procurement import state
    from shared.procurement.planning import generate_plan

    lease = await state.claim_task(pool, "evaluation-sequential", task_id=task_id)
    failures = []
    for attempt in range(2):
        task = await state.begin_planning(pool, lease, repair=attempt > 0)
        plan = await generate_plan(task, repair=attempt > 0, failures=failures)
        task = await state.save_plan(pool, lease, plan)
        outcomes = {}
        for step in plan.steps:
            deps = {key: outcomes[key] for key in step.depends_on}
            key = coarse_reuse_key(task, step, deps)
            cached = await state.find_reusable(pool, lease, step.capability, key)
            if cached:
                outcome = await state.mark_reused(pool, lease, task["plan_revision"], step.id, key, cached)
            else:
                attempt_id, _ = await state.begin_step(pool, lease, task["plan_revision"], step.id, key)
                outcome = await dispatcher(task, step, deps)
                outcome = await state.finish_step(pool, lease, task["plan_revision"], step.id, attempt_id, outcome)
            outcomes[step.id] = outcome
            payload = {"step": step, "outcome": outcome, "reused": bool(cached)}
            await event_hook("after_capability", task, payload)
            if step.capability == "search_candidates":
                await event_hook("after_discovery", task, payload)
        failures = outcome_failures(outcomes)
        if not failures:
            await event_hook("before_finalize", task, {"outcomes": outcomes})
            final = await finalizer(task, outcomes)
            failures = final.get("failures", [])
            if final["status"] == "completed":
                await state.finish_task(
                    pool, lease, "completed", options=final["options"], verification=final.get("verification")
                )
                return
        if failures:
            await state.record_failures(pool, lease, failures)
    await state.finish_task(pool, lease, "needs_review", stop_reason="一次自动修复后仍未通过事实核验")


async def run_trial(pool, case: dict, variant: str, repetition: int = 0) -> dict:
    from unittest.mock import patch

    from shared.procurement import dispatch, state
    from shared.procurement.domain import DomainError, execute_capability
    from shared.procurement.models import ProcurementSpec, StepOutcome
    from shared.procurement.planning import generate_plan

    ids = await seed_case(pool, case)
    events = LogicalEvents(pool, case, ids)
    task = await state.create_task(pool, OWNER, case["description"])
    task = await state.confirm_spec(pool, task["id"], OWNER, task["revision"], ProcurementSpec(**case["spec"]))
    task = await state.queue_task(pool, task["id"], OWNER, task["revision"])
    active, peak = 0, 0
    calls = []

    async def domain_dispatcher(current, step, dependencies):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            inputs = dispatch.step_inputs(current, step, dependencies)
            data = await execute_capability(pool, step.capability, current["requirements"], inputs, OWNER)
            calls.append({"step": step.id, "capability": step.capability, "inputs": inputs})
            return dispatch.outcome_from_data(current, step.capability, data, source="deterministic_tools")
        except DomainError as exc:
            return StepOutcome(
                status="failed",
                error_code=exc.code,
                message=str(exc),
                source="deterministic_tools",
                data={"capability": step.capability, "affected_product_ids": exc.product_ids},
            )
        finally:
            active -= 1

    started = time.perf_counter()
    error = None
    try:
        with patch.object(dispatch, "get_pool", return_value=pool):
            if variant == "sequential_adapter":
                await sequential_adapter(pool, task["id"], domain_dispatcher, dispatch.finalize_task, events)
            else:
                from shared.procurement.runner import ProcurementRunner

                runner = ProcurementRunner(
                    pool,
                    generate_plan,
                    domain_dispatcher,
                    dispatch.finalize_task,
                    reuse_key=dispatch.reuse_key,
                    event_hook=events,
                    enabled=True,
                    concurrency=1 if variant == "no_parallel" else 2,
                    max_repairs=0 if variant == "no_local_repair" else 1,
                )
                if not await runner.run_once(task["id"]):
                    raise RuntimeError("隔离评测运行器未认领已排队任务")
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    elapsed_ms = (time.perf_counter() - started) * 1000
    result = await state.get_task(pool, task["id"], OWNER)
    score = await reference_check(pool, case, ids, result["options"])
    completed = result["status"] == "completed"
    counters = result["counters"]
    return {
        "case_id": case["id"],
        "split": case["split"],
        "variant": variant,
        "repetition": repetition,
        "expected_feasible": case["expected"]["feasible"],
        "status": result["status"],
        "task_success": completed and score["passed"],
        "safe_noncompletion": result["status"] == "needs_review" and not result["options"],
        "safety_violation": completed and not score["passed"],
        "reference": score,
        "final_verification": result["results"].get("_final_verification"),
        "failure_history": [
            {"plan_revision": entry.get("plan_revision"), "failures": entry.get("failures", [])}
            for entry in result["receipt_history"]
            if entry.get("kind") == "repair_requested"
        ],
        "options": result["options"],
        "counters": counters,
        "bounded": counters.get("repairs_used", 0) <= (0 if variant == "no_local_repair" else 1)
        and counters.get("planning_attempts", 0) <= 2
        and counters.get("model_calls", 0) == 0,
        "elapsed_ms": elapsed_ms,
        "peak_domain_calls": peak,
        "domain_calls": calls,
        "scheduled_events": case["events"],
        "fired_event_indexes": events.fired,
        "logical_timeline": events.timeline,
        "error": error,
        "stop_reason": result["stop_reason"],
        "receipts": [
            {
                "step_id": step["step_id"],
                "status": step["status"],
                "attempt": step["attempt"],
                "reused_from": step["reused_from"],
                "error_code": (step.get("outcome") or {}).get("error_code"),
                "message": (step.get("outcome") or {}).get("message", ""),
                "affected_product_ids": (step.get("outcome") or {}).get("data", {}).get("affected_product_ids", []),
            }
            for step in result["steps"]
        ],
    }


def summarize(trials: list[dict]) -> dict:
    summary = {}
    for variant in VARIANTS:
        rows = [row for row in trials if row["variant"] == variant]
        feasible = [row for row in rows if row["expected_feasible"]]
        durations = sorted(row["elapsed_ms"] for row in rows if row.get("metrics_complete", True))
        summary[variant] = {
            "trials": len(rows),
            "feasible_trials": len(feasible),
            "task_success": sum(row["task_success"] for row in rows),
            "feasible_success": sum(row["task_success"] for row in feasible),
            "safe_noncompletion": sum(row["safe_noncompletion"] for row in rows),
            "safety_violations": sum(row["safety_violation"] for row in rows),
            "bounded_trials": sum(row["bounded"] for row in rows),
            "exceptions": sum(row["error"] is not None or row["stop_reason"] == "RUN_FAILED" for row in rows),
            "failed_status": sum(row["status"] == "failed" for row in rows),
            "incomplete_metrics": sum(not row.get("metrics_complete", True) for row in rows),
            "tool_calls": sum(len(row["domain_calls"]) for row in rows),
            "reused_steps": sum(row["counters"].get("reused_steps", 0) for row in rows),
            "p50_ms": statistics.median(durations) if durations else None,
            "p95_ms_descriptive": durations[min(int(len(durations) * 0.95), len(durations) - 1)] if durations else None,
            "peak_domain_calls": max((row["peak_domain_calls"] for row in rows), default=0),
            "by_split": {
                split: {
                    "trials": sum(row["split"] == split for row in rows),
                    "task_success": sum(row["task_success"] and row["split"] == split for row in rows),
                }
                for split in ("dev", "holdout")
            },
        }
    return summary


async def evaluate(pool, dataset, repetitions=1) -> list[dict]:
    trials = []
    for repetition in range(repetitions):
        for index, case in enumerate(dataset["cases"]):
            # 相同case各策略均重建同源数据；轮换顺序减少固定先后效应。
            variants = VARIANTS[index % len(VARIANTS) :] + VARIANTS[: index % len(VARIANTS)]
            for variant in variants:
                try:
                    result = await run_trial(pool, case, variant, repetition)
                    result["metrics_complete"] = True
                except Exception as exc:
                    result = {
                        "case_id": case["id"],
                        "split": case["split"],
                        "variant": variant,
                        "repetition": repetition,
                        "expected_feasible": case["expected"]["feasible"],
                        "status": "harness_error",
                        "final_verification": None,
                        "failure_history": [],
                        "task_success": False,
                        "safe_noncompletion": False,
                        "safety_violation": False,
                        "reference": {"passed": False, "issues": ["harness_error"]},
                        "options": [],
                        "counters": {},
                        "bounded": False,
                        "elapsed_ms": 0,
                        "peak_domain_calls": 0,
                        "domain_calls": [],
                        "scheduled_events": case["events"],
                        "fired_event_indexes": [],
                        "logical_timeline": [],
                        "receipts": [],
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                        "stop_reason": "HARNESS_ERROR",
                        "metrics_complete": False,
                    }
                trials.append(result)
                print(
                    json.dumps(
                        {
                            "case": case["id"],
                            "variant": variant,
                            "status": result["status"],
                            "task_success": result["task_success"],
                            "error": result["error"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    return trials


def main():
    import asyncpg
    from testcontainers.community.postgres import PostgresContainer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1, choices=(1, 2, 3))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出已存在；请使用新文件名以保留原始结果")
    dataset = load_dataset()
    hashes = source_manifest()
    before = datetime.now(UTC).isoformat()

    async def run(url):
        conn = await asyncpg.connect(url)
        try:
            await conn.execute((ROOT / "docker/postgres/init.sql").read_text())
        finally:
            await conn.close()
        pool = await asyncpg.create_pool(url, min_size=1, max_size=12)
        try:
            return await evaluate(pool, dataset, args.repetitions)
        finally:
            await pool.close()

    with PostgresContainer("pgvector/pgvector:pg16", dbname=DB_NAME) as container:
        url = container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        trials = asyncio.run(run(url))
    report = {
        "meta": {
            "started_at": before,
            "finished_at": datetime.now(UTC).isoformat(),
            "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "worktree_dirty": bool(
                subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
            ),
            "python": platform.python_version(),
            "packages": {name: importlib.metadata.version(name) for name in ("agent-framework-core", "asyncpg")},
            "database_image": "pgvector/pgvector:pg16",
            "model_calls": 0,
            "provider_cost": "0",
            "source_hashes": hashes,
            "source_unchanged_during_run": hashes == source_manifest(),
            "scope": "真实隔离PG和MAF工程机制；工具直连，非服务HTTP、真实模型质量或生产收益实验",
            "baseline": "旧ready[0]顺序推进策略的采购适配器，复用相同PG步骤回执，粗粒度依赖/TTL失效",
            "limitations": [
                "采购适配器不是旧query API原生采购能力；所有策略使用同一结构化确认输入和最终SQL oracle",
                "固定合成任务、人审尚待完成；不测开放语言解析或真实模型规划质量",
                "重复试验不是独立场景，不预设统计显著性或延迟收益",
                "低延迟本地工具与新MAF持久checkpoint成本不同，不外推真实远程Agent速度",
            ],
        },
        "dataset": {
            "path": str(DATASET.relative_to(ROOT)),
            "cases": 30,
            "dev": 18,
            "holdout": 12,
            "human_review_status": dataset["human_review_status"],
        },
        "summary": summarize(trials),
        "trials": trials,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
