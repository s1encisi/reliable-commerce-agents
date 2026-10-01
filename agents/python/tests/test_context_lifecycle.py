"""约束生命周期：修订、撤销、换目标、旧窗口重放和并发编辑。"""

import json

import pytest
import pytest_asyncio
from fastapi import HTTPException

from shared.context import current_user_email
from shared.context_pipeline import TaskContext, build_context, persistent_context, revise_context


def test_budget_revision_retires_old_constraint_and_history():
    first = build_context("预算不超过500元", [])
    later = build_context("预算改为800元", [{"role": "user", "content": "预算不超过500元"}], pinned=first)
    assert later.constraints == ["预算改为800元"]
    assert later.constraint_records[0]["status"] == "superseded"
    assert "500" not in json.dumps(later.messages(), ensure_ascii=False)
    assert first.constraints == ["预算不超过500元"]


def test_explicit_revoke_cannot_be_resurrected_by_history():
    first = build_context("预算不能超过500元", [])
    revoked = build_context("取消预算限制", [], pinned=first)
    assert revoked.constraints == []
    later = build_context("继续比较", [{"role": "user", "content": first.goal}], pinned=revoked)
    assert later.constraints == []
    assert later.constraint_records[0]["status"] == "revoked"


def test_new_goal_clears_evidence_constraints_and_history_but_keeps_audit():
    first = build_context("预算不超过500元", [])
    first.evidence_refs = ["product:old"]
    first.resolved_entities = ["product:old"]
    first.receipts = ["receipt:old"]
    changed = build_context("新目标：查询订单", [{"role": "user", "content": first.goal}], pinned=first)
    assert changed.goal == "查询订单"
    assert not changed.constraints and not changed.evidence_refs and not changed.receipts
    assert changed.history == []
    assert changed.goal_history[0]["goal"] == first.goal
    assert changed.constraint_records[0]["status"] == "revoked"


def test_composite_budget_constraint_is_not_silently_deleted():
    first = build_context("预算不超过500元，不要入耳式", [])
    changed = build_context("预算改为800元", [], pinned=first)
    assert "不要入耳式" in changed.constraints[0]
    assert changed.unresolved
    edited = revise_context(changed, constraints=["预算改为800元", "不要入耳式"])
    assert edited.constraints == ["预算改为800元", "不要入耳式"]
    assert edited.unresolved == []


def test_legacy_snapshot_can_be_edited_and_budget_applies_to_prompt_only():
    old = TaskContext(goal="选耳机", constraints=["预算不超过500元", "必须黑色"])
    edited = revise_context(old, constraints=["必须黑色"])
    later = build_context("继续", [], pinned=edited, max_bytes=500)
    assert later.constraints == ["必须黑色"]
    assert any(r["text"] == "预算不超过500元" and r["status"] == "revoked" for r in later.constraint_records)
    assert all(m["role"] == "user" for m in later.messages())


@pytest_asyncio.fixture
async def context_owner(clean_db, monkeypatch):
    import shared.db

    monkeypatch.setattr(shared.db, "_pool", clean_db)
    uid = await clean_db.fetchval(
        "INSERT INTO users(email,password_hash,name) VALUES('context-owner@example.test','hash','Owner') RETURNING id"
    )
    cid = await clean_db.fetchval("INSERT INTO conversations(user_id,title) VALUES($1,'context') RETURNING id", uid)
    token = current_user_email.set("context-owner@example.test")
    try:
        yield clean_db, cid
    finally:
        current_user_email.reset(token)


async def test_context_edit_is_versioned_and_preserves_retired_records(context_owner):
    from orchestrator.routes.context_state import ContextEdit, edit_context, get_context

    _, cid = context_owner
    await persistent_context("预算不超过500元", [], str(cid))
    original = await get_context(cid)
    changed = await edit_context(cid, ContextEdit(revision=original["revision"], constraints=["预算800元"]))
    assert changed["revision"] == original["revision"] + 1
    assert changed["context"]["constraints"] == ["预算800元"]
    with pytest.raises(HTTPException) as exc:
        await edit_context(cid, ContextEdit(revision=original["revision"], constraints=[]))
    assert exc.value.status_code == 409
    later = await persistent_context("继续", [{"role": "user", "content": "预算不超过500元"}], str(cid))
    assert later.constraints == ["预算800元"]
    assert any(r["status"] == "revoked" for r in later.constraint_records)


async def test_context_owner_is_required_for_read_and_edit(context_owner):
    from orchestrator.routes.context_state import ContextEdit, edit_context, get_context

    _, cid = context_owner
    token = current_user_email.set("other@example.test")
    try:
        for action in [get_context(cid), edit_context(cid, ContextEdit(revision=0, goal="窃取资料"))]:
            with pytest.raises(HTTPException) as exc:
                await action
            assert exc.value.status_code == 404
    finally:
        current_user_email.reset(token)


async def test_first_edit_initializes_owned_context_and_goal_change_invalidates_evidence(context_owner):
    from orchestrator.routes.context_state import ContextEdit, edit_context, get_context

    _, cid = context_owner
    assert (await get_context(cid))["revision"] == 0
    first = await edit_context(cid, ContextEdit(revision=0, goal="挑选耳机", constraints=["必须黑色"]))
    changed = await edit_context(cid, ContextEdit(revision=first["revision"], goal="查物流"))
    assert changed["context"]["constraints"] == []
    assert changed["context"]["goal_history"]
