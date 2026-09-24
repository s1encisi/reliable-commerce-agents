"""真实工具的角色授权接线测试。

覆盖商家回复、履约规划、预订、取消订单、修改订单及退款所需角色。
通用装饰器行为另有测试；此处确认每个真实工具使用正确角色集合，
并在访问数据库前拒绝未授权调用。
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
import pytest_asyncio

import shared.db as shared_db
from inventory_fulfillment.tools import calculate_fulfillment_plan, place_backorder
from order_management.tools import cancel_order, modify_order
from review_sentiment.tools import draft_seller_response
from shared.config import settings
from shared.context import current_user_email, current_user_role
from shared.tools.return_tools import process_refund

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db_pool(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    """允许角色的调用使用 clean_db 连接池。"""
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


@pytest.fixture(autouse=True)
def _enable_guardrails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GUARDRAILS_ENABLED", True, raising=False)
    current_user_role.set("")
    current_user_email.set("")


# ─────────────────────── seller/admin-only tools ─────────────────────────────


async def test_draft_seller_response_denied_for_customer() -> None:
    current_user_role.set("customer")
    result = await draft_seller_response(review_id=str(uuid.uuid4()))
    assert result["error"] == "permission_denied"
    assert "seller" in result["required_roles"]


async def test_draft_seller_response_allowed_for_seller(db_pool: asyncpg.Pool) -> None:
    current_user_role.set("seller")
    result = await draft_seller_response(review_id=str(uuid.uuid4()))
    # 角色守卫已通过，随机标识仅导致业务查无结果。
    assert result["error"] != "permission_denied"
    assert result["error"].startswith("Review not found")


async def test_draft_seller_response_allowed_for_admin(db_pool: asyncpg.Pool) -> None:
    current_user_role.set("admin")
    result = await draft_seller_response(review_id=str(uuid.uuid4()))
    assert result["error"] != "permission_denied"


async def test_calculate_fulfillment_plan_denied_for_customer() -> None:
    current_user_role.set("customer")
    result = await calculate_fulfillment_plan(product_ids=[], destination_region="east")
    assert result["error"] == "permission_denied"
    assert "seller" in result["required_roles"]


async def test_calculate_fulfillment_plan_allowed_for_seller() -> None:
    current_user_role.set("seller")
    # 空商品列表在访问数据库前返回，
    # 无需种子数据即可证明角色守卫已放行。
    result = await calculate_fulfillment_plan(product_ids=[], destination_region="east")
    assert result == {"error": "No product IDs provided"}


async def test_place_backorder_denied_for_customer() -> None:
    current_user_role.set("customer")
    result = await place_backorder(product_id=str(uuid.uuid4()), quantity=1)
    assert result["error"] == "permission_denied"
    assert "seller" in result["required_roles"]


async def test_place_backorder_allowed_for_seller() -> None:
    current_user_role.set("seller")
    # 进入工具自身的缺用户守卫，
    # 说明外层角色校验已通过。
    result = await place_backorder(product_id=str(uuid.uuid4()), quantity=1)
    assert result["error"] != "permission_denied"


# ─────────────────────── customer/seller/admin tools ─────────────────────────


async def test_cancel_order_denied_with_no_role() -> None:
    current_user_role.set("")
    result = await cancel_order(order_id=str(uuid.uuid4()), reason="changed my mind")
    assert result["error"] == "permission_denied"


async def test_cancel_order_allowed_for_customer() -> None:
    current_user_role.set("customer")
    result = await cancel_order(order_id=str(uuid.uuid4()), reason="changed my mind")
    assert result["error"] != "permission_denied"


async def test_modify_order_denied_with_no_role() -> None:
    current_user_role.set("")
    result = await modify_order(
        order_id=str(uuid.uuid4()),
        new_address={"street": "1 Main St", "city": "Springfield", "state": "IL", "zip": "62701", "country": "US"},
    )
    assert result["error"] == "permission_denied"


async def test_modify_order_allowed_for_customer() -> None:
    current_user_role.set("customer")
    result = await modify_order(
        order_id=str(uuid.uuid4()),
        new_address={"street": "1 Main St", "city": "Springfield", "state": "IL", "zip": "62701", "country": "US"},
    )
    assert result["error"] != "permission_denied"


async def test_process_refund_denied_with_no_role() -> None:
    current_user_role.set("")
    result = await process_refund(return_id=str(uuid.uuid4()))
    assert result["error"] == "permission_denied"


async def test_process_refund_allowed_for_customer() -> None:
    current_user_role.set("customer")
    result = await process_refund(return_id=str(uuid.uuid4()))
    assert result["error"] != "permission_denied"


async def test_process_refund_allowed_for_admin() -> None:
    current_user_role.set("admin")
    result = await process_refund(return_id=str(uuid.uuid4()))
    assert result["error"] != "permission_denied"


# ─────────────────────── disabled bypass (representative) ────────────────────


async def test_guardrails_disabled_bypasses_tool_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GUARDRAILS_ENABLED", False)
    current_user_role.set("customer")
    result = await calculate_fulfillment_plan(product_ids=[], destination_region="east")
    # 护栏关闭后跳过角色校验，
    # 应到达工具自身参数校验，而非权限拒绝。
    assert result == {"error": "No product IDs provided"}
