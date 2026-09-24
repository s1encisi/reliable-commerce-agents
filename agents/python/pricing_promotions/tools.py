"""定价与促销工具 —— 优惠券校验、购物车优化、优惠活动、捆绑销售。"""

from __future__ import annotations

import json
from datetime import UTC
from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.context import current_user_email
from shared.db import get_pool


@tool(
    name="validate_coupon",
    description=(
        "Validate a coupon code. Checks expiry, min spend, usage limit, applicabl"
        "e categories, and user-specific restrictions."
    ),
)
async def validate_coupon(
    code: Annotated[str, Field(description="Coupon code to validate")],
    cart_total: Annotated[float, Field(description="Current cart total before discount")],
    category: Annotated[str | None, Field(description="Product category to check applicability")] = None,
) -> dict:
    email = current_user_email.get()
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, code, description, discount_type, discount_value,
                      min_spend, max_discount, usage_limit, times_used,
                      valid_from, valid_until, applicable_categories,
                      user_specific_email, is_active
               FROM coupons WHERE UPPER(code) = UPPER($1)""",
            code,
        )
        if not row:
            return {"valid": False, "error": f"Coupon '{code}' not found"}

        # 检查是否启用
        if not row["is_active"]:
            return {"valid": False, "code": code, "error": "Coupon is no longer active"}

        # 检查有效期
        if row["valid_until"]:
            from datetime import datetime

            now = datetime.now(UTC)
            if now > row["valid_until"]:
                return {"valid": False, "code": code, "error": "Coupon has expired"}
            if now < row["valid_from"]:
                return {"valid": False, "code": code, "error": "Coupon is not yet valid"}

        # 检查使用次数上限
        if row["usage_limit"] is not None and row["times_used"] >= row["usage_limit"]:
            return {"valid": False, "code": code, "error": "Coupon usage limit reached"}

        # 检查最低消费门槛
        if row["min_spend"] and cart_total < float(row["min_spend"]):
            return {
                "valid": False,
                "code": code,
                "error": f"Minimum spend of ${float(row['min_spend']):.2f} not met (cart: ${cart_total:.2f})",
            }

        # 检查适用品类
        if row["applicable_categories"] and category:
            if category not in row["applicable_categories"]:
                return {
                    "valid": False,
                    "code": code,
                    ("error"): (
                        f"Coupon not valid for category '{category}'. "
                        f"Valid for: {', '.join(row['applicable_categories'])}"
                    ),
                }

        # 检查用户限定
        if row["user_specific_email"] and row["user_specific_email"] != email:
            return {"valid": False, "code": code, "error": "This coupon is restricted to a specific user"}

        # 计算折扣
        discount_type = row["discount_type"]
        discount_value = float(row["discount_value"])
        if discount_type == "percentage":
            discount_amount = cart_total * (discount_value / 100)
            if row["max_discount"]:
                discount_amount = min(discount_amount, float(row["max_discount"]))
        else:
            discount_amount = min(discount_value, cart_total)

        return {
            "valid": True,
            "code": row["code"],
            "description": row["description"],
            "discount_type": discount_type,
            "discount_value": discount_value,
            "discount_amount": round(discount_amount, 2),
            "new_total": round(cart_total - discount_amount, 2),
            "applicable_categories": row["applicable_categories"],
        }


def _as_list(value: object) -> list[str]:
    """归一化某个规则字段，它可能是标量也可能是列表。

    `promotions.rules` 是无类型的 JSONB，并且种子数据并不一致：
    `buy_x_get_y` 用单数的 `category`，`flash_sale` 用复数的
    `categories`。只读取其中一种写法，正是这两个促销活动都静默匹配不到
    任何东西的原因。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def _pct(value: object) -> float:
    """从无类型 JSONB 中取出百分比，若不是百分比则返回 0.0。"""
    try:
        pct = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    # 负数或超过 100% 的折扣属于数据错误，而不是更大的折扣。
    return pct if 0.0 <= pct <= 100.0 else 0.0


@tool(
    name="optimize_cart",
    description=(
        "Find the best combination of coupons, promotions, and loyalty discounts "
        "for a cart. Returns the optimal savings breakdown."
    ),
)
async def optimize_cart(
    product_ids_with_quantities: Annotated[
        list[dict],
        Field(description='List of items: [{"product_id": "uuid", "quantity": 1}, ...]'),
    ],
) -> dict:
    email = current_user_email.get()
    pool = get_pool()
    async with pool.acquire() as conn:
        # 获取商品详情
        cart_items = []
        for item in product_ids_with_quantities:
            pid = item.get("product_id", "")
            qty = item.get("quantity", 1)
            row = await conn.fetchrow(
                "SELECT id, name, price, category FROM products WHERE id = $1",
                pid,
            )
            if not row:
                return {"error": f"Product not found: {pid}"}
            cart_items.append(
                {
                    "product_id": str(row["id"]),
                    "name": row["name"],
                    "price": float(row["price"]),
                    "category": row["category"],
                    "quantity": qty,
                    "subtotal": float(row["price"]) * qty,
                }
            )

        original_total = sum(i["subtotal"] for i in cart_items)
        categories = list({i["category"] for i in cart_items})
        product_ids = [i["product_id"] for i in cart_items]
        product_names = [i["name"] for i in cart_items]
        savings = []

        # 1. 查找适用的优惠券
        coupons = await conn.fetch(
            """SELECT code, description, discount_type, discount_value,
                      min_spend, max_discount, applicable_categories,
                      user_specific_email
               FROM coupons
               WHERE is_active = TRUE
                 AND (valid_until IS NULL OR valid_until > NOW())
                 AND valid_from <= NOW()
                 AND (usage_limit IS NULL OR times_used < usage_limit)
                 AND (user_specific_email IS NULL OR user_specific_email = $1)
               ORDER BY discount_value DESC""",
            email,
        )

        best_coupon = None
        best_coupon_savings = 0.0
        for c in coupons:
            # 检查最低消费门槛
            if c["min_spend"] and original_total < float(c["min_spend"]):
                continue
            # 检查品类适用性
            if c["applicable_categories"]:
                if not any(cat in c["applicable_categories"] for cat in categories):
                    continue
            # 计算可省金额
            if c["discount_type"] == "percentage":
                amount = original_total * (float(c["discount_value"]) / 100)
                if c["max_discount"]:
                    amount = min(amount, float(c["max_discount"]))
            else:
                amount = min(float(c["discount_value"]), original_total)
            if amount > best_coupon_savings:
                best_coupon_savings = amount
                best_coupon = c

        if best_coupon:
            savings.append(
                {
                    "type": "coupon",
                    "code": best_coupon["code"],
                    "description": best_coupon["description"],
                    "amount": round(best_coupon_savings, 2),
                }
            )

        # 2. 查找适用的促销活动
        promos = await conn.fetch(
            """SELECT name, type, rules
               FROM promotions
               WHERE is_active = TRUE
                 AND start_date <= NOW()
                 AND end_date >= NOW()""",
        )
        for promo in promos:
            rules = promo["rules"] if isinstance(promo["rules"], dict) else json.loads(promo["rules"])
            promo_type = promo["type"]

            if promo_type == "bundle":
                # 同时接受 id 和名称。`scripts/seed.py` 把商品 *名称* 写在
                # `products` 下；这里过去只读 `product_ids`，因此 `required`
                # 始终为空 —— 而对空列表做 `all()` 为 True，于是每个捆绑促销
                # 都"匹配"了每个购物车，随后贡献了 £0，因为下面的求和找不到
                # 任何 id 在这个同样为空的列表里的商品。自促销数据种下以来，
                # 每一次购物车优化调用都在产生这种静默噪音。
                required_ids = [str(x) for x in rules.get("product_ids", [])]
                required_names = [str(x) for x in rules.get("products", [])]
                if not required_ids and not required_names:
                    continue  # 绝不因空集合而匹配

                in_cart = (
                    all(pid in product_ids for pid in required_ids)
                    if required_ids
                    else all(name in product_names for name in required_names)
                )
                if not in_cart:
                    continue

                bundle_total = sum(
                    i["subtotal"] for i in cart_items if i["product_id"] in required_ids or i["name"] in required_names
                )
                amount = bundle_total * (_pct(rules.get("discount_pct")) / 100)
                if amount > 0:
                    savings.append(
                        {
                            "type": "bundle_promotion",
                            "name": promo["name"],
                            "amount": round(amount, 2),
                        }
                    )

            elif promo_type == "buy_x_get_y":
                # 存在两种规则形态，因为种子数据用的是第二种，而这里过去
                # 只理解第一种。"Buy 2 Books Get 10% Off" 是达到最低数量后
                # 的百分比折扣，并不是送赠品式的买一送一 —— 把它当作后者
                # 会导致 `buy_quantity`/`free_quantity` 都默认为 0，于是
                # `quantity >= 0 + 0` 恒为真，下一行除以零。那会让整个工具
                # 崩溃（#51）。
                cats = _as_list(rules.get("categories") or rules.get("category"))
                buy_qty = int(rules.get("buy_quantity") or 0)
                free_qty = int(rules.get("free_quantity") or 0)
                min_qty = int(rules.get("min_quantity") or 0)
                discount_pct = _pct(rules.get("discount_pct"))

                for item in cart_items:
                    if cats and item["category"] not in cats:
                        continue

                    if buy_qty > 0 and free_qty > 0:
                        # 真正的买 X 送 Y。
                        group = buy_qty + free_qty
                        if item["quantity"] < group:
                            continue
                        free_units = item["quantity"] // group * free_qty
                        amount = item["price"] * free_units
                    elif min_qty > 0 and discount_pct > 0:
                        # 达到最低数量后按百分比打折。
                        if item["quantity"] < min_qty:
                            continue
                        amount = item["subtotal"] * (discount_pct / 100)
                    else:
                        # 规则不属于任何已知形态 —— 跳过，而不是猜测。
                        continue

                    if amount > 0:
                        savings.append(
                            {
                                "type": "buy_x_get_y",
                                "name": promo["name"],
                                "product": item["name"],
                                "amount": round(amount, 2),
                            }
                        )

            elif promo_type == "flash_sale":
                # 又是同样的不匹配：种子数据用 `categories` 来限定闪购，
                # 这里过去只读 `product_ids`，所以没有任何商品能匹配上，
                # 该促销成了静默的空操作。
                flash_ids = [str(x) for x in rules.get("product_ids", [])]
                flash_cats = _as_list(rules.get("categories") or rules.get("category"))
                if not flash_ids and not flash_cats:
                    continue

                discount_pct = _pct(rules.get("discount_pct"))
                for item in cart_items:
                    matches = item["product_id"] in flash_ids or item["category"] in flash_cats
                    if not matches:
                        continue
                    amount = item["subtotal"] * (discount_pct / 100)
                    if amount > 0:
                        savings.append(
                            {
                                "type": "flash_sale",
                                "name": promo["name"],
                                "product": item["name"],
                                "amount": round(amount, 2),
                            }
                        )

        # 3. 计算会员折扣
        if email:
            user = await conn.fetchrow(
                """SELECT u.loyalty_tier, lt.discount_pct
                   FROM users u
                   JOIN loyalty_tiers lt ON lt.name = u.loyalty_tier
                   WHERE u.email = $1""",
                email,
            )
            if user and float(user["discount_pct"]) > 0:
                loyalty_amount = original_total * (float(user["discount_pct"]) / 100)
                savings.append(
                    {
                        "type": "loyalty_discount",
                        "tier": user["loyalty_tier"],
                        "discount_pct": float(user["discount_pct"]),
                        "amount": round(loyalty_amount, 2),
                    }
                )

        total_savings = sum(s["amount"] for s in savings)
        final_total = max(0, original_total - total_savings)

        return {
            "cart_items": cart_items,
            "original_total": round(original_total, 2),
            "savings": savings,
            "total_savings": round(total_savings, 2),
            "final_total": round(final_total, 2),
            "savings_percentage": round((total_savings / original_total) * 100, 1) if original_total > 0 else 0,
        }


@tool(name="get_active_deals", description="List all currently active promotions and non-expired coupons.")
async def get_active_deals() -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        coupons = await conn.fetch(
            """SELECT code, description, discount_type, discount_value,
                      min_spend, max_discount, valid_until, applicable_categories
               FROM coupons
               WHERE is_active = TRUE
                 AND (valid_until IS NULL OR valid_until > NOW())
                 AND valid_from <= NOW()
                 AND (usage_limit IS NULL OR times_used < usage_limit)
                 AND user_specific_email IS NULL
               ORDER BY discount_value DESC""",
        )

        promotions = await conn.fetch(
            """SELECT name, type, rules, start_date, end_date
               FROM promotions
               WHERE is_active = TRUE
                 AND start_date <= NOW()
                 AND end_date >= NOW()
               ORDER BY end_date ASC""",
        )

        return {
            "coupons": [
                {
                    "code": c["code"],
                    "description": c["description"],
                    "discount_type": c["discount_type"],
                    "discount_value": float(c["discount_value"]),
                    "min_spend": float(c["min_spend"]) if c["min_spend"] else None,
                    "max_discount": float(c["max_discount"]) if c["max_discount"] else None,
                    "valid_until": c["valid_until"].isoformat() if c["valid_until"] else None,
                    "applicable_categories": c["applicable_categories"],
                }
                for c in coupons
            ],
            "promotions": [
                {
                    "name": p["name"],
                    "type": p["type"],
                    "rules": p["rules"] if isinstance(p["rules"], dict) else json.loads(p["rules"]),
                    "start_date": p["start_date"].isoformat(),
                    "end_date": p["end_date"].isoformat(),
                }
                for p in promotions
            ],
            "total_deals": len(coupons) + len(promotions),
        }


@tool(name="check_bundle_eligibility", description="Check if a set of products qualifies for any bundle promotions.")
async def check_bundle_eligibility(
    product_ids: Annotated[list[str], Field(description="List of product UUIDs to check for bundle deals")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        # 获取商品详情
        products = []
        for pid in product_ids:
            row = await conn.fetchrow(
                "SELECT id, name, price, category FROM products WHERE id = $1",
                pid,
            )
            if row:
                products.append(
                    {
                        "product_id": str(row["id"]),
                        "name": row["name"],
                        "price": float(row["price"]),
                        "category": row["category"],
                    }
                )

        if not products:
            return {"eligible": False, "error": "No valid products found"}

        # 检查捆绑促销
        promos = await conn.fetch(
            """SELECT name, type, rules, start_date, end_date
               FROM promotions
               WHERE is_active = TRUE
                 AND type = 'bundle'
                 AND start_date <= NOW()
                 AND end_date >= NOW()""",
        )

        eligible_bundles = []
        for promo in promos:
            rules = promo["rules"] if isinstance(promo["rules"], dict) else json.loads(promo["rules"])
            required_ids = rules.get("product_ids", [])
            required_categories = rules.get("categories", [])

            # 按商品 ID 检查
            if required_ids:
                matching = [pid for pid in product_ids if pid in required_ids]
                if len(matching) == len(required_ids):
                    discount_pct = rules.get("discount_pct", 0)
                    bundle_total = sum(p["price"] for p in products if p["product_id"] in required_ids)
                    savings = bundle_total * (discount_pct / 100)
                    eligible_bundles.append(
                        {
                            "promotion_name": promo["name"],
                            "discount_pct": discount_pct,
                            "bundle_total": round(bundle_total, 2),
                            "savings": round(savings, 2),
                            "end_date": promo["end_date"].isoformat(),
                            "qualifying_products": [p["name"] for p in products if p["product_id"] in required_ids],
                        }
                    )

            # 按品类检查
            if required_categories:
                cart_categories = [p["category"] for p in products]
                if all(cat in cart_categories for cat in required_categories):
                    discount_pct = rules.get("discount_pct", 0)
                    matching_products = [p for p in products if p["category"] in required_categories]
                    bundle_total = sum(p["price"] for p in matching_products)
                    savings = bundle_total * (discount_pct / 100)
                    eligible_bundles.append(
                        {
                            "promotion_name": promo["name"],
                            "discount_pct": discount_pct,
                            "bundle_total": round(bundle_total, 2),
                            "savings": round(savings, 2),
                            "end_date": promo["end_date"].isoformat(),
                            "qualifying_products": [p["name"] for p in matching_products],
                        }
                    )

        # 同时检查 buy_x_get_y 促销
        bxgy_promos = await conn.fetch(
            """SELECT name, type, rules, end_date
               FROM promotions
               WHERE is_active = TRUE
                 AND type = 'buy_x_get_y'
                 AND start_date <= NOW()
                 AND end_date >= NOW()""",
        )

        bxgy_eligible = []
        for promo in bxgy_promos:
            rules = promo["rules"] if isinstance(promo["rules"], dict) else json.loads(promo["rules"])
            applicable_cats = rules.get("categories", [])
            buy_qty = rules.get("buy_quantity", 0)
            free_qty = rules.get("free_quantity", 0)
            matching = [p for p in products if not applicable_cats or p["category"] in applicable_cats]
            if matching:
                bxgy_eligible.append(
                    {
                        "promotion_name": promo["name"],
                        "buy_quantity": buy_qty,
                        "free_quantity": free_qty,
                        "applicable_products": [p["name"] for p in matching],
                        "end_date": promo["end_date"].isoformat(),
                    }
                )

        return {
            "eligible": len(eligible_bundles) > 0 or len(bxgy_eligible) > 0,
            "products_checked": [p["name"] for p in products],
            "bundle_deals": eligible_bundles,
            "buy_x_get_y_deals": bxgy_eligible,
        }
