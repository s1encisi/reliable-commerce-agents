"""促销规则与种子数据字段契约回归测试。

历史实现把套装 products 当作 product_ids，空条件匹配所有购物车；
买赠字段不一致导致除零；限时促销分类与商品标识混淆导致不生效。
使用真实种子规则形态，防止数据和消费代码再次漂移。
"""

from __future__ import annotations

import json
import uuid

import pytest

from shared.context import current_user_email


async def _product(db, name: str, category: str, price: float) -> str:
    product_id = uuid.uuid4()
    await db.execute(
        """INSERT INTO products (id, name, description, category, brand, price, is_active)
           VALUES ($1, $2, 'test product', $3, 'test', $4, TRUE)""",
        product_id,
        name,
        category,
        price,
    )
    return str(product_id)


async def _promotion(db, name: str, type_: str, rules: dict) -> None:
    await db.execute(
        """INSERT INTO promotions (name, type, rules, start_date, end_date, is_active)
           VALUES ($1, $2, $3, NOW() - INTERVAL '1 day', NOW() + INTERVAL '30 days', TRUE)""",
        name,
        type_,
        json.dumps(rules),
    )


async def _optimize(db, monkeypatch, cart):
    monkeypatch.setattr("shared.db._pool", db, raising=False)
    current_user_email.set("promo-test@example.com")
    from pricing_promotions.tools import optimize_cart

    fn = getattr(optimize_cart, "func", optimize_cart)
    return await fn(cart)


@pytest.mark.asyncio
async def test_buy_x_get_y_with_seeded_rule_shape_does_not_crash(clean_db, monkeypatch) -> None:
    """种子使用 min_quantity 与 discount_pct，不能按其他字段读成零后除零。

    模型层捕获异常并不代表促销工具正确执行，必须直接验证工具结果。
    """
    book = await _product(clean_db, "Deep Work", "Books", 20.00)
    await _promotion(
        clean_db,
        "Buy 2 Books Get 10% Off",
        "buy_x_get_y",
        {"category": "Books", "min_quantity": 2, "discount_pct": 10},
    )

    result = await _optimize(clean_db, monkeypatch, [{"product_id": book, "quantity": 3}])

    bogo = [s for s in result["savings"] if s["type"] == "buy_x_get_y"]
    assert bogo, f"promotion did not apply: {result['savings']}"
    assert bogo[0]["amount"] == pytest.approx(6.00)  # 10% of 3 x $20


@pytest.mark.asyncio
async def test_buy_x_get_y_still_supports_real_free_units(clean_db, monkeypatch) -> None:
    """另一种真正买赠的规则形态也必须保持可用。"""
    item = await _product(clean_db, "Socks", "Clothing", 10.00)
    await _promotion(
        clean_db,
        "Buy 2 Get 1 Free",
        "buy_x_get_y",
        {"categories": ["Clothing"], "buy_quantity": 2, "free_quantity": 1},
    )

    result = await _optimize(clean_db, monkeypatch, [{"product_id": item, "quantity": 6}])

    bogo = [s for s in result["savings"] if s["type"] == "buy_x_get_y"]
    assert bogo and bogo[0]["amount"] == pytest.approx(20.00)  # 赠送两件。


@pytest.mark.asyncio
async def test_a_bundle_requiring_nothing_never_matches(clean_db, monkeypatch) -> None:
    """all([]) 为真，空套装条件不能误匹配全部购物车。"""
    item = await _product(clean_db, "Random Thing", "Home", 50.00)
    await _promotion(clean_db, "Malformed Bundle", "bundle", {"discount_pct": 25})

    result = await _optimize(clean_db, monkeypatch, [{"product_id": item, "quantity": 1}])

    assert not [s for s in result["savings"] if s["type"] == "bundle_promotion"]


@pytest.mark.asyncio
async def test_bundle_matches_on_product_names(clean_db, monkeypatch) -> None:
    """种子套装按商品名配置，而非随机生成的 UUID。"""
    a = await _product(clean_db, "Sony WH-1000XM5", "Electronics", 300.00)
    b = await _product(clean_db, "Samsung T7 Shield SSD 2TB", "Electronics", 100.00)
    await _promotion(
        clean_db,
        "Tech Bundle Deal",
        "bundle",
        {"products": ["Sony WH-1000XM5", "Samsung T7 Shield SSD 2TB"], "discount_pct": 10},
    )

    result = await _optimize(
        clean_db, monkeypatch, [{"product_id": a, "quantity": 1}, {"product_id": b, "quantity": 1}]
    )

    bundle = [s for s in result["savings"] if s["type"] == "bundle_promotion"]
    assert bundle and bundle[0]["amount"] == pytest.approx(40.00)  # 10% of $400


@pytest.mark.asyncio
async def test_flash_sale_scoped_by_category_applies(clean_db, monkeypatch) -> None:
    """按分类限制的促销不能被错误当作商品标识列表。"""
    shirt = await _product(clean_db, "Shirt", "Clothing", 80.00)
    book = await _product(clean_db, "A Book", "Books", 20.00)
    await _promotion(
        clean_db,
        "Spring Flash Sale",
        "flash_sale",
        {"categories": ["Clothing", "Sports"], "discount_pct": 15},
    )

    result = await _optimize(
        clean_db, monkeypatch, [{"product_id": shirt, "quantity": 1}, {"product_id": book, "quantity": 1}]
    )

    flash = [s for s in result["savings"] if s["type"] == "flash_sale"]
    assert len(flash) == 1, "the flash sale must not apply to the out-of-category item"
    assert flash[0]["amount"] == pytest.approx(12.00)  # 15% of $80


@pytest.mark.asyncio
async def test_malformed_rules_are_skipped_not_guessed_at(clean_db, monkeypatch) -> None:
    """规则无法解析时不产生优惠，也不能抛错。

    手工 JSONB 数据错误不能静默转化为折扣。
    """
    item = await _product(clean_db, "Widget", "Home", 100.00)
    await _promotion(clean_db, "Nonsense BOGO", "buy_x_get_y", {"category": "Home"})
    await _promotion(clean_db, "Negative Sale", "flash_sale", {"categories": ["Home"], "discount_pct": -50})
    await _promotion(clean_db, "Impossible Sale", "flash_sale", {"categories": ["Home"], "discount_pct": 900})

    result = await _optimize(clean_db, monkeypatch, [{"product_id": item, "quantity": 5}])

    assert result["total_savings"] == 0
    assert result["final_total"] == pytest.approx(500.00)
