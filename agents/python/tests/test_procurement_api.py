"""采购HTTP控制面与真实授权/持久化边界，使用隔离PostgreSQL。"""

import httpx
import pytest
from fastapi import FastAPI

OWNER = "procure-api@example.test"
OTHER = "other-procure@example.test"
GOAL = "采购3副采购专用耳机，货款预算1500元，需要两个独立备选方案。"


@pytest.fixture
async def api_client(clean_db, monkeypatch):
    import shared.db
    import shared.jwt_utils as jwt_utils
    from orchestrator.routes import diagnostics, legacy, procurement, tasks
    from shared.config import Settings
    from shared.context import current_session_id, current_user_email, current_user_role
    from shared.procurement import model_io

    config = Settings(
        _env_file=None,
        PROCUREMENT_ENABLED=True,
        PROCUREMENT_MODE="offline",
        JWT_SECRET="j" * 40,
        AGENT_SHARED_SECRET="s" * 40,
        AUTH_MODE="local",
    )
    for module in (jwt_utils, legacy, tasks, procurement, diagnostics, model_io):
        monkeypatch.setattr(module, "settings", config)
    monkeypatch.setattr(shared.db, "_pool", clean_db)
    ids = {}
    for email in (OWNER, OTHER):
        ids[email] = await clean_db.fetchval(
            "INSERT INTO users(email,password_hash,name) VALUES($1,'unused','Synthetic') RETURNING id", email
        )
    app = FastAPI()
    app.include_router(procurement.router)
    app.include_router(tasks.router)
    app.include_router(diagnostics.router)
    headers = {
        email: {"Authorization": "Bearer " + jwt_utils.create_access_token(email, "customer", str(uid))}
        for email, uid in ids.items()
    }
    tokens = [(var, var.set("")) for var in (current_user_email, current_user_role, current_session_id)]
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test", headers=headers[OWNER]
        ) as client:
            yield client, clean_db, headers, config
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


async def draft(client, goal=GOAL):
    response = await client.post("/api/tasks/parse", json={"goal": goal})
    assert response.status_code == 201, response.text
    return response.json()


def spec(data):
    return {key: value for key, value in data["draft_spec"].items() if not key.startswith("_")}


async def confirmed(client):
    data = await draft(client)
    response = await client.post(
        f"/api/tasks/{data['id']}/confirm-spec", json={"revision": data["revision"], "requirements": spec(data)}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_requires_authentication_before_creating_task(api_client):
    client, pool, _, _ = api_client
    response = await client.post("/api/tasks/parse", json={"goal": GOAL}, headers={"Authorization": ""})
    assert response.status_code == 401
    assert await pool.fetchval("SELECT count(*) FROM agent_tasks") == 0


async def test_missing_quantity_cannot_start_without_confirmation(api_client):
    client, _, _, _ = api_client
    data = await draft(client, "采购耳机，预算1500元")
    assert data["status"] == "needs_input"
    assert data["draft_spec"]["quantity"] is None
    response = await client.post(f"/api/tasks/{data['id']}/start", json={"revision": data["revision"]})
    assert response.status_code == 409
    assert data["counters"]["model_calls"] == 0


async def test_confirm_then_queue_is_cas_and_does_not_run_legacy_steps(api_client):
    client, _, _, _ = api_client
    data = await confirmed(client)
    assert data["status"] == "pending" and data["spec_revision"] == 1
    response = await client.post(f"/api/tasks/{data['id']}/start", json={"revision": data["revision"]})
    assert response.status_code == 202
    again = await client.post(f"/api/tasks/{data['id']}/start", json={"revision": data["revision"]})
    assert again.status_code == 409
    for operation in ("plan", "next", "recover", "confirm"):
        body = {"revision": response.json()["revision"]}
        if operation == "confirm":
            body["confirmed"] = True
        rejected = await client.post(f"/api/tasks/{data['id']}/{operation}", json=body)
        assert rejected.status_code == 409


async def test_foreign_owner_cannot_read_control_or_stream(api_client):
    client, _, headers, _ = api_client
    data = await confirmed(client)
    assert (await client.get(f"/api/tasks/{data['id']}", headers=headers[OTHER])).status_code == 404
    assert (await client.get(f"/api/tasks/{data['id']}/events", headers=headers[OTHER])).status_code == 404
    for operation in ("start", "pause"):
        result = await client.post(
            f"/api/tasks/{data['id']}/{operation}", json={"revision": data["revision"]}, headers=headers[OTHER]
        )
        assert result.status_code == 404


async def test_unhandled_shipping_constraint_cannot_be_silently_confirmed(api_client):
    client, _, _, _ = api_client
    data = await draft(client, GOAL + "必须明天送到。")
    assert data["draft_spec"]["_parse"]["unsupported_conditions"]
    response = await client.post(
        f"/api/tasks/{data['id']}/confirm-spec", json={"revision": data["revision"], "requirements": spec(data)}
    )
    assert response.status_code == 422


async def test_model_switch_and_allowlist_are_enforced_before_any_model_call(api_client):
    client, pool, headers, config = api_client
    config.PROCUREMENT_ALLOWLIST = OTHER
    assert (await client.post("/api/tasks/parse", json={"goal": GOAL})).status_code == 403
    caps = (await client.get("/api/runtime/capabilities")).json()
    assert not caps["procurement"]["enabled"]
    config.PROCUREMENT_ALLOWLIST = ""
    config.PROCUREMENT_MODE = "model"
    assert (await client.post("/api/tasks/parse", json={"goal": GOAL})).status_code == 403
    assert await pool.fetchval("SELECT count(*) FROM agent_tasks") == 0


async def test_draft_patch_synchronizes_goal_and_requires_new_confirmation(api_client):
    client, _, _, _ = api_client
    data = await draft(client)
    updated_spec = {**spec(data), "total_budget": "1200.00"}
    response = await client.patch(
        f"/api/tasks/{data['id']}",
        json={
            "revision": data["revision"],
            "goal": GOAL.replace("1500", "1200"),
            "constraints": [],
            "requirements": updated_spec,
        },
    )
    assert response.status_code == 200, response.text
    revised = response.json()
    assert "1200" in revised["goal"]
    assert revised["status"] == "draft" and revised["spec_revision"] == 0
    final = await client.post(
        f"/api/tasks/{data['id']}/confirm-spec", json={"revision": revised["revision"], "requirements": updated_spec}
    )
    assert final.status_code == 200
    assert final.json()["requirements"]["total_budget"] == "1200.00"


async def test_events_are_read_only_and_resume_after_last_sequence(api_client):
    client, pool, _, _ = api_client
    data = await draft(client)
    response = await client.get(f"/api/tasks/{data['id']}/events?after_seq=1")
    assert response.status_code == 200
    assert "id: 1\n" not in response.text
    assert "spec.draft_updated" in response.text
    after = (await client.get(f"/api/tasks/{data['id']}")).json()
    assert after["revision"] == data["revision"] and after["state_seq"] == data["state_seq"]
