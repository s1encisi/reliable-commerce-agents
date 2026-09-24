"""工作流模式的真实检查点持久化测试。

确认购前和退货工作流实际写入数据库，每次保存产生检查点事件，
完成及暂停事件携带最新检查点；恢复时用全新 Workflow 对象，
仅依据 PostgreSQL 状态继续执行。
"""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.modes.base import RunContext
from orchestrator.modes.workflow_mode import PrePurchaseMode, ReturnReplaceMode
from shared.config import settings
from shared.context import current_user_email

PRODUCT_UUID = "11111111-1111-1111-1111-111111111111"
ORDER_UUID = "22222222-2222-2222-2222-222222222222"


async def _sentiment_ok(product_id: str) -> dict[str, Any]:
    return {"sentiment": "positive", "total_reviews": 42}


async def _stock_ok(product_id: str) -> dict[str, Any]:
    return {"in_stock": True, "total_quantity": 17}


async def _price_good(product_id: str, days: int) -> dict[str, Any]:
    return {"is_good_deal": True, "average_price": 120.5, "trend": "flat"}


async def _shipping_fast(product_id: str, destination_region: str) -> dict[str, Any]:
    return {"options": [{"price": 4.99, "days": 2}]}


PRE_PURCHASE_TOOLS = {
    "analyze_sentiment": _sentiment_ok,
    "check_stock": _stock_ok,
    "get_price_history": _price_good,
    "estimate_shipping": _shipping_fast,
}


@pytest.mark.asyncio
async def test_pre_purchase_mode_persists_checkpoints_to_postgres(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    mode = PrePurchaseMode(tools=PRE_PURCHASE_TOOLS)
    events = [e async for e in mode.run(PRODUCT_UUID, RunContext(history=[]))]

    checkpoint_events = [e for e in events if e.kind == "checkpoint"]
    assert checkpoint_events, "expected at least one kind=checkpoint event"
    assert all(e.payload["workflow_name"] == "pre-purchase" for e in checkpoint_events)

    final = events[-1]
    assert final.kind == "run_completed"
    latest_id = final.payload["latest_checkpoint_id"]
    assert latest_id is not None
    assert latest_id == checkpoint_events[-1].payload["checkpoint_id"]

    row = await clean_db.fetchrow("SELECT workflow_name FROM workflow_checkpoints WHERE checkpoint_id = $1", latest_id)
    assert row is not None
    assert row["workflow_name"] == "pre-purchase"

    count = await clean_db.fetchval("SELECT COUNT(*) FROM workflow_checkpoints WHERE workflow_name = 'pre-purchase'")
    assert count == len(checkpoint_events)


async def _eligible(order_id: str) -> dict[str, Any]:
    return {"eligible": True}


async def _initiate_ok(order_id: str, reason: str, refund_method: str) -> dict[str, Any]:
    return {"return_id": "ret-99", "refund_amount": 120.0}


async def _search_ok(max_price: float, min_rating: float, limit: int) -> list[dict[str, Any]]:
    return [{"id": "p-1", "name": "Replacement A"}]


async def _tier_gold() -> dict[str, Any]:
    return {"tier": "gold", "discount_pct": 10.0}


RETURN_TOOLS = {
    "check_return_eligibility": _eligible,
    "initiate_return": _initiate_ok,
    "search_products": _search_ok,
    "get_loyalty_tier": _tier_gold,
}


def _stub_order_details(total: float):
    async def _get_order_details(*, order_id: str) -> dict[str, Any]:
        return {"order_id": order_id, "total": total}

    return _get_order_details


@pytest.mark.asyncio
async def test_return_replace_mode_pause_carries_request_id_and_checkpoint(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import order_management.tools as order_tools

    high = settings.RETURN_HITL_THRESHOLD + 100.0
    monkeypatch.setattr(order_tools, "get_order_details", _stub_order_details(high))
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    current_user_email.set("alice@example.com")

    mode = ReturnReplaceMode(tools=RETURN_TOOLS)
    events = [e async for e in mode.run(f"return order {ORDER_UUID}", RunContext(history=[]))]

    final = events[-1]
    assert final.kind == "run_completed"
    assert final.payload["pending_approval"] is True
    assert final.payload["request_id"], "resume needs MAF's own pause token"
    assert final.payload["latest_checkpoint_id"] is not None

    row = await clean_db.fetchrow(
        "SELECT workflow_name FROM workflow_checkpoints WHERE checkpoint_id = $1",
        final.payload["latest_checkpoint_id"],
    )
    assert row is not None and row["workflow_name"] == "return-and-replace"


@pytest.mark.asyncio
async def test_return_replace_mode_resume_completes_a_fresh_workflow_object(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跨请求恢复：旧实例暂停，全新实例只依据数据库状态继续执行。"""
    import order_management.tools as order_tools

    high = settings.RETURN_HITL_THRESHOLD + 100.0
    monkeypatch.setattr(order_tools, "get_order_details", _stub_order_details(high))
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    current_user_email.set("alice@example.com")

    paused_mode = ReturnReplaceMode(tools=RETURN_TOOLS)
    events = [e async for e in paused_mode.run(f"return order {ORDER_UUID}", RunContext(history=[]))]
    final = events[-1]
    assert final.payload["pending_approval"] is True
    request_id = final.payload["request_id"]
    checkpoint_id = final.payload["latest_checkpoint_id"]

    resuming_mode = ReturnReplaceMode(tools=RETURN_TOOLS)
    resume_events = [
        e async for e in resuming_mode.resume(checkpoint_id=checkpoint_id, request_id=request_id, approved=True)
    ]

    resume_final = resume_events[-1]
    assert resume_final.kind == "run_completed"
    # 审批响应按快照重建最小工作流状态，
    # 不能假定所有暂停前内存字段都仍存在，
    # 包括原先的 return_id。
    # 应断言真正需要跨恢复保留的字段，
    # 与退货工作流恢复测试保持一致。
    assert "approved and finalized" in resume_final.payload["text"]
    assert "finalize" in resume_final.payload["agents_involved"]
    assert resume_final.payload["pending_approval"] is False
    # 恢复过程的折扣和收尾超步也会保存检查点。
    assert resume_final.payload["latest_checkpoint_id"] is not None
    assert resume_final.payload["latest_checkpoint_id"] != checkpoint_id


@pytest.mark.asyncio
async def test_return_replace_mode_resume_rejection_stops_before_finalize(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import order_management.tools as order_tools

    high = settings.RETURN_HITL_THRESHOLD + 100.0
    monkeypatch.setattr(order_tools, "get_order_details", _stub_order_details(high))
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    current_user_email.set("bob@example.com")

    paused_mode = ReturnReplaceMode(tools=RETURN_TOOLS)
    events = [e async for e in paused_mode.run(f"return order {ORDER_UUID}", RunContext(history=[]))]
    final = events[-1]
    request_id = final.payload["request_id"]
    checkpoint_id = final.payload["latest_checkpoint_id"]

    resuming_mode = ReturnReplaceMode(tools=RETURN_TOOLS)
    resume_events = [
        e async for e in resuming_mode.resume(checkpoint_id=checkpoint_id, request_id=request_id, approved=False)
    ]

    resume_final = resume_events[-1]
    assert "finalize" not in resume_final.payload["agents_involved"]
    assert "rejected by reviewer" in resume_final.payload["text"]
