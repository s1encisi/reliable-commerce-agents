"""隔离 PostgreSQL 与真实服务进程上的 HTTP 对照；所有数据均为本文件合成。"""

import asyncio
import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import httpx
from testcontainers.community.postgres import PostgresContainer

ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT / ".local/upgrade-evaluation"
WORK.mkdir(parents=True, exist_ok=True)
PRODUCT = "11111111-1111-4111-8111-111111111111"
SECOND = "22222222-2222-4222-8222-222222222222"
ORDER = "44444444-4444-4444-8444-444444444444"
USER = "33333333-3333-4333-8333-333333333333"


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def seed(url):
    conn = await asyncpg.connect(url)
    try:
        await conn.execute((ROOT / "docker/postgres/init.sql").read_text())
        from shared.jwt_utils import hash_password

        await conn.execute(
            "INSERT INTO users(id,email,password_hash,name,role) "
            "VALUES($1,'eval-owner@example.test',$2,'合成用户','customer')",
            UUID(USER),
            hash_password("SyntheticEvalOnly!"),
        )
        await conn.execute("INSERT INTO loyalty_tiers(name,min_spend,discount_pct) VALUES('bronze',0,0)")
        for pid, name, price, quantity in [
            (PRODUCT, "Cloud ANC 无线 耳机", 399, 20),
            (SECOND, "Studio 头戴 耳机", 499, 5),
        ]:
            await conn.execute(
                "INSERT INTO "
                "products(id,name,description,category,brand,price,ratin"
                "g,is_active) VALUES($1,$2,'合成评测商品，头戴式耳机','Electronics',"
                "'DemoBrand',$3,4.0,TRUE)",
                UUID(pid),
                name,
                price,
            )
            warehouse = await conn.fetchval(
                "INSERT INTO warehouses(name,location,region) VALUES('East','上海','east') RETURNING id"
            )
            await conn.execute("INSERT INTO warehouse_inventory VALUES($1,$2,$3,10)", warehouse, UUID(pid), quantity)
            for rating, body in [(5, "佩戴舒适，续航满意"), (4, "声音不错，稍微有些重"), (2, "隔音不够好")]:
                await conn.execute(
                    "INSERT INTO "
                    "reviews(product_id,user_id,rating,title,body,verified_p"
                    "urchase) VALUES($1,$2,$3,'合成评论',$4,TRUE)",
                    UUID(pid),
                    UUID(USER),
                    rating,
                    body,
                )
            await conn.execute(
                "INSERT INTO price_history(product_id,price,recorded_at) "
                "VALUES($1,$2,NOW()-INTERVAL '10 days'),($1,$3,NOW())",
                UUID(pid),
                price + 50,
                price,
            )
        await conn.execute(
            "INSERT INTO orders(id,user_id,status,total,shipping_address) VALUES($1,$2,'delivered',399,'{}'::jsonb)",
            UUID(ORDER),
            UUID(USER),
        )
        await conn.execute(
            "INSERT INTO order_items(order_id,product_id,quantity,unit_price,subtotal) VALUES($1,$2,1,399,399)",
            UUID(ORDER),
            UUID(PRODUCT),
        )
        await conn.execute(
            "INSERT INTO order_status_history(order_id,status,timestamp) "
            "VALUES($1,'delivered',NOW()-INTERVAL '5 days')",
            UUID(ORDER),
        )
        await conn.execute(
            "INSERT INTO "
            "coupons(code,description,discount_type,discount_value,v"
            "alid_until) VALUES('SAVE10','合成九折券','percentage',10,NOW"
            "()+INTERVAL '10 days')"
        )
        await conn.execute("ANALYZE")
    finally:
        await conn.close()


CASES = [
    ("e01", f"查询商品 {PRODUCT} 的完整信息和价格，不要修改任何数据。", ["get_product_details"], "399"),
    ("e02", "列出 Electronics 分类中500元以下的商品，不要修改购物车。", ["search_products"], "399"),
    ("e03", f"比较商品 {PRODUCT} 和 {SECOND} 的价格与规格，只查询。", ["compare_products"], "499"),
    ("e04", f"我自己的订单 {ORDER} 的总额和状态是什么？", ["get_order_details"], "399"),
    ("e05", "列出我已经签收的订单。", ["get_user_orders"], "399"),
    ("e06", f"检查商品 {PRODUCT} 的当前库存，只查询。", ["check_stock"], "20"),
    ("e07", f"商品 {SECOND} 的库存够买十件吗？只查询，不下单。", ["check_stock"], "5"),
    ("e08", f"展示商品 {PRODUCT} 的价格历史。", ["get_price_history"], "449"),
    ("e09", "列出当前有效的优惠券或促销。", ["get_active_deals"], "SAVE10"),
    ("e10", f"查询商品 {PRODUCT} 的用户评论，不要生成新评论。", ["get_product_reviews"], "舒适"),
    ("e11", f"分析商品 {PRODUCT} 的评价情绪，用真实评论作依据。", ["analyze_sentiment"], None),
    ("e12", f"查询我自己的订单 {ORDER} 是否满足退货资格，只检查，不能提交申请。", ["check_return_eligibility"], None),
]


async def run_http_evaluation(dataset, results, save):
    base_env = os.environ.copy()
    base_env.update(
        LLM_PROVIDER="deepseek",
        LLM_MODEL="deepseek-flash",
        EMBEDDING_PROVIDER="none",
        RATE_LIMIT_ENABLED="false",
        VERIFIED_OUTPUT_ONLY="true",
        GROUNDING_MODE="enforce",
        AUTH_MODE="local",
        EVALUATION_MODE="true",
        JWT_SECRET=secrets.token_hex(32),
        AGENT_SHARED_SECRET=secrets.token_hex(32),
        PYTHONUNBUFFERED="1",
        GENAI_CAPTURE_CONTENT="false",
    )
    processes = []
    handles = []
    container = PostgresContainer("pgvector/pgvector:pg16", dbname="ecommerce_upgrade_eval")
    await asyncio.to_thread(container.start)
    url = container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    await seed(url)
    base_env["DATABASE_URL"] = url
    services = [
        "product_discovery",
        "order_management",
        "pricing_promotions",
        "review_sentiment",
        "inventory_fulfillment",
    ]
    registry = {name.replace("_", "-"): f"http://127.0.0.1:{port()}" for name in services}
    base_env["AGENT_REGISTRY"] = json.dumps(registry)
    python = str(ROOT / "agents/python/.venv/bin/python")

    def start(name, service_port, env):
        log = (WORK / f"{name}-{service_port}.log").open("w")
        handles.append(log)
        proc = subprocess.Popen(
            [python, "-m", "uvicorn", name + ".main:app", "--host", "127.0.0.1", "--port", str(service_port)],
            cwd=ROOT / "agents/python",
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        processes.append(proc)
        return proc

    async def ready(endpoint):
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(80):
                try:
                    r = await client.get(endpoint + "/health")
                    if r.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
        raise RuntimeError("isolated service failed readiness")

    try:
        specialist_env = base_env | {"MOONSHOT_API_KEY": "", "TYPESAFE_API_KEY": ""}
        for name in services:
            start(name, int(registry[name.replace("_", "-")].rsplit(":", 1)[1]), specialist_env)
        await asyncio.gather(*(ready(endpoint) for endpoint in registry.values()))
        for group, mode, context in [
            ("A", "off", "false"),
            ("B", "off", "true"),
            ("C", "active", "false"),
            ("D", "active", "true"),
        ]:
            env = base_env | {
                "DECISION_ROUTING_MODE": mode,
                "DECISION_CONTEXT_ENABLED": context,
                "DECISION_MIN_PROBABILITY": str(results["threshold"]),
            }
            service_port = port()
            proc = start("orchestrator", service_port, env)
            endpoint = f"http://127.0.0.1:{service_port}"
            await ready(endpoint)
            async with httpx.AsyncClient(base_url=endpoint, timeout=120) as client:
                login = await client.post(
                    "/api/auth/login", json={"email": "eval-owner@example.test", "password": "SyntheticEvalOnly!"}
                )
                login.raise_for_status()
                token = login.json()["access_token"]
                for case_id, message, expected_tools, needle in CASES:
                    if any(x["group"] == group and x["id"] == case_id for x in results["http"]):
                        continue
                    from shared.paid_transport import configured_budget

                    before_cost = configured_budget().snapshot()
                    start_time = time.monotonic()
                    entry = {"group": group, "id": case_id, "expected_tools": expected_tools}
                    try:
                        response = await client.post(
                            "/api/chat",
                            json={"message": message, "mode": "decision-router"},
                            headers={"Authorization": "Bearer " + token},
                        )
                        body = response.json()
                        entry["http_status"] = response.status_code
                        text = body.get("response", "")
                        entry.update(response=text, decision=body.get("decision"), grounding=body.get("grounding"))
                        conn = await asyncpg.connect(url)
                        try:
                            rows = await conn.fetch(
                                "SELECT s.tool_name,s.status,s.tool_output FROM "
                                "agent_execution_steps s JOIN usage_logs l ON l.id=s.usa"
                                "ge_log_id WHERE l.session_id=$1::uuid",
                                body.get("conversation_id"),
                            )
                        finally:
                            await conn.close()
                        entry["tools"] = [r["tool_name"].split(":")[-1] for r in rows]
                        entry["step_statuses"] = [r["status"] for r in rows]
                        entry["passed_contract"] = (
                            response.status_code == 200
                            and bool(set(expected_tools) & set(entry["tools"]))
                            and (needle is None or needle in text)
                        )
                        entry["manual_review_required"] = True
                    except Exception as exc:
                        entry.update(error_type=type(exc).__name__, passed_contract=False)
                    entry["latency_ms"] = (time.monotonic() - start_time) * 1000
                    after_cost = configured_budget().snapshot()
                    entry["provider_cost_ceiling"] = {
                        p: round(after_cost[p]["committed_or_reserved"] - before_cost[p]["committed_or_reserved"], 8)
                        for p in after_cost
                    }
                    entry["provider_calls"] = {p: after_cost[p]["calls"] - before_cost[p]["calls"] for p in after_cost}
                    entry["provider_tokens"] = {
                        p: {
                            k: after_cost[p].get(k, 0) - before_cost[p].get(k, 0)
                            for k in ["input_tokens", "output_tokens"]
                        }
                        for p in after_cost
                    }
                    results["http"].append(entry)
                    save()
                    print("HTTP", group, case_id, entry.get("http_status"), entry["passed_contract"], flush=True)
                    # 基础设施连续失败时不继续消耗模型额度。
                    recent = [x for x in results["http"] if x["group"] == group][-3:]
                    if len(recent) == 3 and all(
                        x.get("error_type") or x.get("http_status", 500) >= 500 for x in recent
                    ):
                        raise RuntimeError("three infrastructure failures; evaluation stopped")
            proc.terminate()
            await asyncio.to_thread(proc.wait, 10)
        # 两种规划器在同一合成状态下生成计划；执行仍由 Flash 驱动只读专业服务。
        from orchestrator.modes.decision_router import dispatch_readonly
        from shared.config import settings
        from shared.context import current_user_email, current_user_role
        from shared.paid_transport import current_root_run
        from shared.task_planner import generate_plan

        settings.AGENT_REGISTRY = json.dumps(registry)
        settings.AGENT_SHARED_SECRET = base_env["AGENT_SHARED_SECRET"]
        current_user_email.set("eval-owner@example.test")
        current_user_role.set("customer")
        for index, item in enumerate(dataset["complex"]):
            for provider in ["deepseek", "moonshot"]:
                if any(x["id"] == index and x["provider"] == provider for x in results["complex"]):
                    continue
                task = {
                    "id": str(uuid4()),
                    "goal": item["goal"],
                    "constraints": item["constraints"],
                    "results": {},
                    "plan": None,
                }
                entry = {"id": index, "provider": provider}
                started = time.monotonic()
                try:
                    plan = await generate_plan(task, provider)
                    entry["plan"] = plan.model_dump()
                    entry["valid_plan"] = True
                    entry["executions"] = []
                    current_root_run.set(task["id"])
                    # 最多八步；每步执行使用相同的只读专业服务，无 K3 业务调用。
                    completed = {}
                    for step in plan.steps:
                        history = [{"role": "user", "content": "约束：" + "；".join(item["constraints"])}]
                        history.extend(
                            {"role": "assistant", "content": completed[key].get("response", "")}
                            for key in step.depends_on
                        )
                        response = await dispatch_readonly(step.service, step.instruction, history)
                        completed[step.id] = response
                        entry["executions"].append({"step": step.id, "response": response})
                except Exception as exc:
                    entry["error_type"] = type(exc).__name__
                entry["latency_ms"] = (time.monotonic() - started) * 1000
                results["complex"].append(entry)
                save()
                print(
                    "COMPLEX",
                    index,
                    provider,
                    entry.get("valid_plan", False),
                    entry.get("error_type", "ok"),
                    flush=True,
                )
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    await asyncio.to_thread(proc.wait, 10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    await asyncio.to_thread(proc.wait)
        for handle in handles:
            handle.close()
        await asyncio.to_thread(container.stop)
