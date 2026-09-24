"""事实台账纯逻辑测试，覆盖商品、订单和促销结果的不同字段形态。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from shared.grounding.ledger import (
    GroundingLedger,
    GroundingLedgerMiddleware,
    current_grounding_ledger,
    reset_grounding_ledger,
)

_PRODUCT_ID = "0fd372fa-ecb2-4db0-bb71-8628a784ced9"


def _function_context(result) -> SimpleNamespace:
    return SimpleNamespace(function=SimpleNamespace(name="some_tool"), result=result)


class _FakeContent:
    """模拟真实 Content 包装：工具 JSON 存于列表首项的 text。

    仅用裸字典测试无法发现实际运行中包装形态不同的问题。"""

    def __init__(self, text: str) -> None:
        self.text = text


async def _noop() -> None:
    return None


def test_reset_grounding_ledger_sets_a_fresh_ledger() -> None:
    fresh = reset_grounding_ledger()
    assert isinstance(fresh, GroundingLedger)
    assert current_grounding_ledger.get() is fresh


@pytest.mark.asyncio
async def test_middleware_is_noop_when_ledger_unset() -> None:
    current_grounding_ledger.set(None)
    ctx = _function_context({"id": _PRODUCT_ID, "name": "X", "price": 5.0})
    await GroundingLedgerMiddleware().process(ctx, _noop)
    # 无需额外断言，只需确认不抛错。


@pytest.mark.asyncio
async def test_records_single_product_result() -> None:
    ledger = reset_grounding_ledger()
    ctx = _function_context({"id": _PRODUCT_ID, "name": "Widget", "price": 19.99, "image_url": "u"})
    await GroundingLedgerMiddleware().process(ctx, _noop)

    fact = ledger.products[_PRODUCT_ID]
    assert fact.name == "Widget"
    assert fact.price == 19.99
    assert fact.image_url == "u"


@pytest.mark.asyncio
async def test_records_list_of_products() -> None:
    ledger = reset_grounding_ledger()
    ctx = _function_context(
        [
            {"id": _PRODUCT_ID, "name": "A", "price": 1.0},
            {"id": "22222222-2222-2222-2222-222222222222", "name": "B", "price": 2.0},
        ]
    )
    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert set(ledger.products) == {_PRODUCT_ID, "22222222-2222-2222-2222-222222222222"}


@pytest.mark.asyncio
async def test_records_order_keyed_by_order_id_not_id() -> None:
    # 订单详情返回 order_id 而非 id，
    # 台账必须正确识别这种真实字段差异。
    ledger = reset_grounding_ledger()
    order_id = "1a2b3c4d-5e6f-7890-abcd-ef0123456789"
    ctx = _function_context(
        {
            "order_id": order_id,
            "status": "shipped",
            "total": 199.99,
            "tracking_number": "TRK1",
            "items": [{"item_id": "x", "product_name": "Widget"}],
        }
    )
    await GroundingLedgerMiddleware().process(ctx, _noop)

    fact = ledger.orders[order_id]
    assert fact.status == "shipped"
    assert fact.total == 199.99
    assert fact.tracking == "TRK1"


@pytest.mark.asyncio
async def test_order_line_items_are_not_recorded_as_products() -> None:
    # 订单明细只有 item_id，没有 product_id，
    # 不能误记为可核验的商品事实。
    ledger = reset_grounding_ledger()
    ctx = _function_context(
        {
            "order_id": "1a2b3c4d-5e6f-7890-abcd-ef0123456789",
            "status": "shipped",
            "total": 50.0,
            "items": [{"item_id": "abc", "product_name": "Widget", "unit_price": 25.0}],
        }
    )
    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert ledger.products == {}


@pytest.mark.asyncio
async def test_records_valid_coupon_as_promo() -> None:
    ledger = reset_grounding_ledger()
    ctx = _function_context(
        {
            "valid": True,
            "code": "save10",
            "discount_type": "percentage",
            "discount_amount": 15.5,
        }
    )
    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert ledger.promos["SAVE10"].discount_amount == 15.5


@pytest.mark.asyncio
async def test_invalid_coupon_result_is_not_recorded() -> None:
    ledger = reset_grounding_ledger()
    ctx = _function_context({"valid": False, "code": "EXPIRED", "error": "Coupon has expired"})
    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert ledger.promos == {}


@pytest.mark.asyncio
async def test_check_stock_result_is_not_recorded_as_product() -> None:
    # 库存结果包含 product_id、in_stock、total_quantity，
    # 不含商品价格和名称，
    # 不能被归入 ProductFact。
    ledger = reset_grounding_ledger()
    ctx = _function_context({"product_id": _PRODUCT_ID, "in_stock": True, "total_quantity": 12})
    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert ledger.products == {}


@pytest.mark.asyncio
async def test_error_shaped_result_is_ignored() -> None:
    ledger = reset_grounding_ledger()
    ctx = _function_context({"error": "Product not found: abc"})
    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert ledger.products == {}
    assert ledger.orders == {}


@pytest.mark.asyncio
async def test_get_price_history_shape_records_current_and_aggregate_prices() -> None:
    # 价格历史工具顶层返回聚合字段，
    # 包括 current_price、average_price、
    # min_price 和 max_price，而非 id、name、price。
    # 因此常规商品、订单、促销形态都不会识别它。
    # 增加 known_amounts 前，
    # 这些真实金额也会被判为无法核验，
    # 本用例防止这一回归。
    ledger = reset_grounding_ledger()
    ctx = _function_context(
        {
            "product_id": _PRODUCT_ID,
            "product_name": "Widget",
            "current_price": 299.99,
            "period_days": 30,
            "average_price": 283.4,
            "min_price": 258.93,
            "max_price": 307.90,
            "trend": "decreasing",
            "is_good_deal": True,
            "data_points": 12,
        }
    )
    await GroundingLedgerMiddleware().process(ctx, _noop)

    assert 299.99 in ledger.known_amounts
    assert 283.4 in ledger.known_amounts
    assert 258.93 in ledger.known_amounts
    assert 307.90 in ledger.known_amounts


@pytest.mark.asyncio
async def test_get_price_history_empty_shape_records_current_price_only() -> None:
    # 无历史记录时又是另一种形态：
    # current_price 和空 history 列表，
    # 仍没有完整的商品顶层字段。
    ledger = reset_grounding_ledger()
    ctx = _function_context(
        {
            "product_id": _PRODUCT_ID,
            "product_name": "Widget",
            "current_price": 199.5,
            "history": [],
            "summary": "No price history available",
        }
    )
    await GroundingLedgerMiddleware().process(ctx, _noop)

    assert 199.5 in ledger.known_amounts


@pytest.mark.asyncio
async def test_records_a_product_from_the_real_content_wrapped_runtime_shape() -> None:
    # 其他测试多使用裸字典，
    # 但生产 context.result 实际是
    # list[Content]，JSON 保存在 text 中。
    # 未解包时，事实台账始终为空，
    # 即使本轮确实执行了工具。
    # 此用例模拟实际包装形态，
    # 其中包含商品价格 JSON，
    # 避免 isinstance(result, dict) 静默跳过，
    # 却没有任何错误信号。
    ledger = reset_grounding_ledger()
    payload = {"id": _PRODUCT_ID, "name": "Widget", "price": 19.99, "image_url": "u"}
    ctx = _function_context([_FakeContent(json.dumps(payload))])
    await GroundingLedgerMiddleware().process(ctx, _noop)

    fact = ledger.products[_PRODUCT_ID]
    assert fact.name == "Widget"
    assert fact.price == 19.99
