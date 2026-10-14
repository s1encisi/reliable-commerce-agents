"""真实ASGI专业服务与认证边界，执行器经过HTTP序列化而不是直接跳过host。"""

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from shared.procurement import state
from shared.procurement.dispatch import dispatch_step, finalize_task, outcome_from_data, reuse_key
from shared.procurement.models import ProcurementSpec
from shared.procurement.planning import CAPABILITY_SERVICES, fixed_plan, generate_plan
from shared.procurement.runner import ProcurementRunner

OWNER = "service-procurement@example.test"


@pytest.fixture
async def runtime(clean_db, monkeypatch):
    import shared.auth as auth
    import shared.db
    import shared.jwt_utils as jwt_utils
    import shared.procurement.dispatch as dispatch
    import shared.procurement.model_io as model_io
    import shared.procurement.service as service
    from shared.agent_host import create_agent_app
    from shared.config import Settings

    config = Settings(
        _env_file=None,
        PROCUREMENT_ENABLED=True,
        PROCUREMENT_MODE="offline",
        JWT_SECRET="j" * 40,
        AGENT_SHARED_SECRET="s" * 40,
        AUTH_MODE="local",
    )
    monkeypatch.setattr(shared.db, "_pool", clean_db)
    for module in (auth, jwt_utils, dispatch, service, model_io):
        monkeypatch.setattr(module, "settings", config)
    import shared.oauth.service_client as service_client

    monkeypatch.setattr(service_client, "settings", config)
    uid = await clean_db.fetchval(
        "INSERT INTO users(email,password_hash,name) VALUES($1,'unused','采购测试人') RETURNING id", OWNER
    )
    wid = await clean_db.fetchval(
        "INSERT INTO warehouses(name,location,region) VALUES('采购仓','本地','east') RETURNING id"
    )
    ids = []
    for index in range(3):
        pid = await clean_db.fetchval(
            "INSERT INTO products(name,description,category,price,rating) "
            "VALUES($1,'headphones procurement','Electronics',$2,4.6) RETURNING id",
            f"采购耳机 headphones {index}",
            200 + index * 10,
        )
        ids.append(str(pid))
        await clean_db.execute(
            "INSERT INTO warehouse_inventory(warehouse_id,product_id,quantity) VALUES($1,$2,10)", wid, pid
        )
        await clean_db.execute(
            "INSERT INTO reviews(product_id,user_id,rating,title,body) VALUES($1,$2,2,'合成评价','佩戴感受因人而异')",
            pid,
            uid,
        )
    apps = {}
    for name in CAPABILITY_SERVICES.values():
        app = create_agent_app(agent=object(), agent_name=name, port=8080)
        app.add_middleware(auth.AgentAuthMiddleware, agent_name=name)
        apps[name] = app
    config.AGENT_REGISTRY = json.dumps({name: f"http://{name}" for name in apps})
    calls = []

    class RoutingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            calls.append((request.url.host, json.loads(request.content)))
            async with httpx.ASGITransport(apps[request.url.host]) as transport:
                return await transport.handle_async_request(request)

    original_client = httpx.AsyncClient

    def client(**kwargs):
        return original_client(transport=RoutingTransport(), **kwargs)

    proxy = SimpleNamespace(
        **{name: getattr(httpx, name) for name in ("TimeoutException", "NetworkError", "HTTPStatusError")},
        AsyncClient=client,
    )
    monkeypatch.setattr(dispatch, "httpx", proxy)
    yield clean_db, apps, calls, config, uid


async def prepared(pool):
    task = await state.create_task(pool, OWNER, "采购3副耳机，预算1500元")
    task = await state.confirm_spec(
        pool, task["id"], OWNER, task["revision"], ProcurementSpec(query="耳机", quantity=3, total_budget="1500.00")
    )
    return await state.queue_task(pool, task["id"], OWNER, task["revision"])


async def test_maf_executes_real_authenticated_specialist_http_contracts(runtime):
    pool, _, calls, _, _ = runtime
    task = await prepared(pool)
    runner = ProcurementRunner(pool, generate_plan, dispatch_step, finalize_task, reuse_key=reuse_key, enabled=True)
    assert await runner.run_once(task["id"])
    result = await state.get_task(pool, task["id"], OWNER)
    assert result["status"] == "completed", result["stop_reason"]
    assert len(result["options"]) == 2
    assert len(calls) == 7
    assert set(name for name, _ in calls) == set(CAPABILITY_SERVICES.values())
    assert result["counters"]["model_calls"] == 0
    assert all(row["outcome"]["source"] == "deterministic_tools" for row in result["steps"])
    assert len(result["artifacts"]) == 7


async def test_user_token_cannot_call_private_procurement_endpoint(runtime):
    _, apps, _, _, uid = runtime
    from shared.jwt_utils import create_access_token

    headers = {"Authorization": "Bearer " + create_access_token(OWNER, "customer", str(uid))}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(apps["product-discovery"]), base_url="http://test", headers=headers
    ) as client:
        result = await client.post("/procurement:execute", json={})
    assert result.status_code == 403


async def test_specialist_inputs_cannot_override_saved_candidate_scope(runtime):
    pool, apps, _, config, _ = runtime
    from shared.procurement.domain import execute_capability

    task = await prepared(pool)
    lease = await state.claim_task(pool, "test-worker", task_id=task["id"])
    task = await state.begin_planning(pool, lease)
    task = await state.save_plan(pool, lease, fixed_plan(task))
    inputs = {"exclude_ids": [], "candidate_limit": 6}
    result = await execute_capability(pool, "search_candidates", task["requirements"], inputs, OWNER)
    attempt, _ = await state.begin_step(pool, lease, task["plan_revision"], "discover", "test-input")
    await state.finish_step(
        pool,
        lease,
        task["plan_revision"],
        "discover",
        attempt,
        outcome_from_data(task, "search_candidates", result, source="deterministic_tools"),
    )
    binding = {
        "task_id": task["id"],
        "owner_email": OWNER,
        "generation": lease.generation,
        "lease_token": str(lease.token),
    }
    body = {
        "task_id": task["id"],
        "plan_revision": task["plan_revision"],
        "step_id": "quote-1",
        "capability": "quote_options",
        "inputs": {"product_ids": [str(uuid4())]},
        "binding": binding,
    }
    headers = {"X-Agent-Secret": config.AGENT_SHARED_SECRET, "X-User-Email": OWNER, "X-User-Role": "customer"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(apps["pricing-promotions"]), base_url="http://test", headers=headers
    ) as client:
        denied = await client.post("/procurement:execute", json=body)
        assert denied.status_code == 409 and "上游证据" in denied.text
        body["inputs"] = {"product_ids": [result["items"][0]["product_id"]]}
        ok = await client.post("/procurement:execute", json=body)
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"]["items"][0]["quantity"] == 3
        body["capability"] = "check_inventory"
        denied_service = await client.post("/procurement:execute", json=body)
        assert denied_service.status_code == 403
