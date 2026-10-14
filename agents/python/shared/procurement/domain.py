"""采购领域只读工具与最终事实核验；金额使用 Decimal，不执行交易。

evidence 必须来自可信 host 捕获并保存的工具回执，而不是模型正文。
source/rule_hash 用于核对来源和规则版本，不是任意调用者的身份证明。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID

from shared.search import expand_catalog_query, or_joined_tsquery

CAPABILITIES = ("search_candidates", "quote_options", "check_inventory", "review_evidence")
POLICY = "single_coupon_v1"
TTL_SECONDS = 300
CENT = Decimal("0.01")


class DomainError(ValueError):
    def __init__(self, code: str, message: str, product_ids: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.product_ids = product_ids or []


def _money(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DomainError("INVALID_SPEC", "金额必须是有限非负数") from exc
    if not result.is_finite() or result < 0:
        raise DomainError("INVALID_SPEC", "金额必须是有限非负数")
    return result.quantize(CENT, rounding=ROUND_HALF_UP)


def _amount(value) -> str:
    return format(_money(value), ".2f")


def _spec(spec: dict) -> dict:
    quantity = spec.get("quantity", 1)
    alternatives = spec.get("alternative_count", 1)
    if type(quantity) is not int or not 1 <= quantity <= 100:
        raise DomainError("INVALID_QUANTITY", "采购数量必须为 1 到 100 的整数")
    if type(alternatives) is not int or not 1 <= alternatives <= 2:
        raise DomainError("INVALID_SPEC", "备选数量必须为 1 或 2")
    query = spec.get("query", "")
    if not isinstance(query, str) or not query.strip():
        raise DomainError("INVALID_SPEC", "采购需求不能为空")
    if spec.get("currency", "CNY") != "CNY" or spec.get("budget_scope", "goods") != "goods":
        raise DomainError("INVALID_SPEC", "仅支持人民币商品金额，不含运费")
    if spec.get("quote_policy", POLICY) != POLICY or spec.get("in_stock", True) is not True:
        raise DomainError("INVALID_SPEC", "仅支持 single_coupon_v1 且要求有货")
    rating = spec.get("min_rating")
    if rating is not None and not Decimal("0") <= _money(rating) <= Decimal("5"):
        raise DomainError("INVALID_SPEC", "最低评分必须为 0 到 5")
    return {
        **spec,
        "query": query.strip(),
        "quantity": quantity,
        "alternative_count": alternatives,
        "total_budget": _money(spec["total_budget"]),
        "min_rating": rating,
    }


def _ids(values, *, maximum: int = 6) -> list[str]:
    if not isinstance(values, list) or len(values) > maximum:
        raise DomainError("INVALID_INPUT", "商品 ID 必须是有界列表")
    try:
        result = [str(UUID(str(value))) for value in values]
    except (ValueError, AttributeError, TypeError) as exc:
        raise DomainError("INVALID_INPUT", "商品 ID 必须是 UUID") from exc
    if len(set(result)) != len(result):
        raise DomainError("INVALID_INPUT", "商品 ID 不得重复")
    return result


def _hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _spec_hash(spec: dict, capability: str, owner: str) -> str:
    fields = {"capability": capability}
    if capability == "search_candidates":
        fields.update(
            query=spec["query"], min_rating=(_amount(spec["min_rating"]) if spec["min_rating"] is not None else None)
        )
    elif capability == "quote_options":
        fields.update(
            quantity=spec["quantity"],
            quote_policy=POLICY,
            currency="CNY",
            owner_hash=hashlib.sha256(owner.encode()).hexdigest(),
        )
    elif capability == "check_inventory":
        fields.update(quantity=spec["quantity"])
    return _hash(fields)


def _coupon_rule(row) -> dict:
    return {
        "id": str(row["id"]),
        "discount_type": row["discount_type"],
        "discount_value": _amount(row["discount_value"]),
        "min_spend": _amount(row["min_spend"] or 0),
        "max_discount": _amount(row["max_discount"]) if row["max_discount"] is not None else None,
        "usage_limit": row["usage_limit"],
        "times_used": row["times_used"],
        "valid_from": row["valid_from"].isoformat() if row["valid_from"] else None,
        "valid_until": row["valid_until"].isoformat() if row["valid_until"] else None,
        "applicable_categories": sorted(row["applicable_categories"]) if row["applicable_categories"] else None,
        # 不在回执中暴露用户邮箱，仅将归属条件纳入规则摘要。
        "owner_hash": hashlib.sha256((row["user_specific_email"] or "*").encode()).hexdigest(),
        "is_active": row["is_active"],
    }


def _discount(row, *, owner: str, category: str, total: Decimal, now: datetime) -> Decimal:
    if not row["is_active"] or (row["user_specific_email"] and row["user_specific_email"] != owner):
        return Decimal("0")
    if row["applicable_categories"] is not None and category not in row["applicable_categories"]:
        return Decimal("0")
    if row["valid_from"] and row["valid_from"] > now or row["valid_until"] and row["valid_until"] <= now:
        return Decimal("0")
    if row["usage_limit"] is not None and (row["times_used"] or 0) >= row["usage_limit"]:
        return Decimal("0")
    if total < _money(row["min_spend"] or 0):
        return Decimal("0")
    value = _money(row["discount_value"])
    if row["discount_type"] == "fixed":
        discount = value
    elif row["discount_type"] == "percentage" and value <= 100:
        discount = (total * value / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    else:
        return Decimal("0")
    if row["max_discount"] is not None:
        discount = min(discount, _money(row["max_discount"]))
    return min(discount, total)


async def _products(conn, ids: list[str]) -> list:
    rows = await conn.fetch("SELECT * FROM products WHERE id=ANY($1::uuid[]) AND is_active", [UUID(i) for i in ids])
    by_id = {str(row["id"]): row for row in rows}
    missing = [pid for pid in ids if pid not in by_id]
    if missing:
        raise DomainError("UNKNOWN_PRODUCT", "商品不存在或已下架", missing)
    return [by_id[pid] for pid in ids]


async def _execute(conn, capability: str, spec: dict, inputs: dict, owner: str, now: datetime) -> dict:
    valid_until = now + timedelta(seconds=TTL_SECONDS)
    if capability == "search_candidates":
        excluded = _ids(inputs.get("exclude_ids", []), maximum=12)
        limit = inputs.get("candidate_limit", 6)
        if type(limit) is not int or not 1 <= limit <= 6:
            raise DomainError("INVALID_INPUT", "candidate_limit 必须为 1 到 6")
        expanded = expand_catalog_query(spec["query"])
        tsq = or_joined_tsquery("$1")
        rows = await conn.fetch(
            f"""SELECT p.*, ts_rank(p.search_vector, {tsq}) AS rank
                FROM products p WHERE p.is_active AND NOT(p.id=ANY($2::uuid[]))
                AND (p.search_vector @@ {tsq} OR p.name ILIKE $3 OR p.description ILIKE $3)
                AND ($4::numeric IS NULL OR p.rating >= $4)
                ORDER BY rank DESC, p.name, p.id LIMIT $5""",
            expanded,
            [UUID(i) for i in excluded],
            "%" + spec["query"] + "%",
            _money(spec["min_rating"]) if spec["min_rating"] is not None else None,
            limit,
        )
        items = [
            {
                "product_id": str(row["id"]),
                "id": str(row["id"]),
                "name": row["name"],
                "price": _amount(row["price"]),
                "category": row["category"],
                "rating": _amount(row["rating"] or 0),
                "source_ids": {"products": [str(row["id"])]},
            }
            for row in rows
        ]
    else:
        ids = _ids(inputs.get("product_ids", []), maximum=2)
        if not ids:
            raise DomainError("INVALID_INPUT", "必须提供上游已核验的商品 ID")
        rows = await _products(conn, ids)
        items = []
        for product in rows:
            pid = str(product["id"])
            if capability == "quote_options":
                total = _money(product["price"]) * spec["quantity"]
                coupons = await conn.fetch("SELECT * FROM coupons ORDER BY id")
                eligible = [
                    (row, _discount(row, owner=owner, category=product["category"], total=total, now=now))
                    for row in coupons
                ]
                eligible = [(row, discount) for row, discount in eligible if discount > 0]
                chosen, discount = max(eligible, key=lambda pair: pair[1]) if eligible else (None, Decimal("0"))
                rule = _coupon_rule(chosen) if chosen else None
                rule_input = {
                    "policy": POLICY,
                    "currency": "CNY",
                    "product_id": pid,
                    "category": product["category"],
                    "unit_price": _amount(product["price"]),
                    "quantity": spec["quantity"],
                    "coupon": rule,
                }
                if chosen and chosen["valid_until"]:
                    valid_until = min(valid_until, chosen["valid_until"])
                items.append(
                    {
                        "product_id": pid,
                        "quantity": spec["quantity"],
                        "unit_price": _amount(product["price"]),
                        "original_total": _amount(total),
                        "discount_amount": _amount(discount),
                        "final_total": _amount(total - discount),
                        "coupon_id": str(chosen["id"]) if chosen else None,
                        "coupon_rule": rule,
                        "rule_hash": _hash(rule_input),
                        "currency": "CNY",
                        "quote_policy": POLICY,
                        "source_ids": {"products": [pid], "coupons": [str(chosen["id"])] if chosen else []},
                    }
                )
            elif capability == "check_inventory":
                inventory = await conn.fetch(
                    "SELECT warehouse_id,quantity FROM warehouse_inventory WHERE product_id=$1 ORDER BY warehouse_id",
                    product["id"],
                )
                quantity = sum(row["quantity"] for row in inventory)
                items.append(
                    {
                        "product_id": pid,
                        "available_quantity": quantity,
                        "requested_quantity": spec["quantity"],
                        "sufficient": quantity >= spec["quantity"],
                        "source_ids": {
                            "products": [pid],
                            "warehouses": [str(row["warehouse_id"]) for row in inventory],
                        },
                    }
                )
            elif capability == "review_evidence":
                reviews = await conn.fetch(
                    """SELECT id,title,body,rating,is_flagged,verified_purchase FROM reviews
                       WHERE product_id=$1 ORDER BY helpful_count DESC,id LIMIT 50""",
                    product["id"],
                )
                count = await conn.fetchval("SELECT count(*) FROM reviews WHERE product_id=$1", product["id"])
                originals = [
                    {
                        "id": str(row["id"]),
                        "title": row["title"],
                        "body": row["body"],
                        "rating": row["rating"],
                        "is_flagged": row["is_flagged"],
                        "verified_purchase": row["verified_purchase"],
                    }
                    for row in reviews
                ]
                risks = [
                    {
                        "review_id": row["id"],
                        "reason": "flagged_review" if row["is_flagged"] else "low_rating",
                        "quote": row["body"],
                    }
                    for row in originals
                    if row["is_flagged"] or row["rating"] <= 2
                ]
                items.append(
                    {
                        "product_id": pid,
                        "reviews": originals,
                        "review_count": count,
                        "sample_limit": 50,
                        "evidence_status": "available" if originals else "insufficient",
                        "risk_references": risks,
                        "analysis_scope": "原文和评分/标记证据；不声称自动中文情感或偏好满足度分析",
                        "source_ids": {"products": [pid], "reviews": [row["id"] for row in originals]},
                    }
                )
            else:
                raise DomainError("UNKNOWN_CAPABILITY", "不支持的采购能力")
    return {
        "capability": capability,
        "spec_hash": _spec_hash(spec, capability, owner),
        "source": f"tool:{capability}",
        "observed_at": now.isoformat(),
        "valid_until": valid_until.isoformat(),
        "items": items,
    }


async def execute_capability(pool, capability: str, spec: dict, inputs: dict, owner_email: str) -> dict:
    """执行一次只读能力。owner_email 必须由可信认证链传入。"""
    if capability not in CAPABILITIES:
        raise DomainError("UNKNOWN_CAPABILITY", "不支持的采购能力")
    normalized = _spec(spec)
    async with pool.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
        if not await conn.fetchval("SELECT 1 FROM users WHERE email=$1 AND is_active", owner_email):
            raise DomainError("UNKNOWN_OWNER", "申请人不存在或已停用")
        return await _execute(conn, capability, normalized, inputs, owner_email, datetime.now(UTC))


def _envelopes(evidence) -> list[dict]:
    if isinstance(evidence, dict):
        if "capability" in evidence:
            return [evidence]
        evidence = list(evidence.values())
    if not isinstance(evidence, list):
        return []
    # 接受直接 data 或已保存 StepOutcome；不接受模型正文中的 JSON。
    return [item.get("data", item) for item in evidence if isinstance(item, dict)]


async def verify_options(pool, spec: dict, options: list[dict], evidence, owner_email: str) -> dict:
    """对候选范围、可信回执和实时事实独立核验；失败可供有界局部修复。

    evidence 是调用者从可信 DB 步骤回执中提取的对象。这一边界须由
    host 鉴权和闭包捕获保证，工具 source 字符串不能替代该边界。
    """
    normalized = _spec(spec)
    now = datetime.now(UTC)
    failures = []

    def fail(pid, code, reason, capabilities=()):
        failures.append(
            {
                "product_id": pid,
                "code": code,
                "reason": reason,
                "affected_product_ids": [pid] if pid else [],
                "capabilities": list(capabilities),
            }
        )

    if not isinstance(options, list):
        options = []
    if len(options) != normalized["alternative_count"]:
        fail(None, "OPTION_COUNT_MISMATCH", "实际备选数量与确认需求不符")
    evidence_by_key = {}
    candidate_ids = set()
    for envelope in _envelopes(evidence):
        capability = envelope.get("capability")
        ids = [item.get("product_id") for item in envelope.get("items", []) if isinstance(item, dict)]
        try:
            observed = datetime.fromisoformat(envelope["observed_at"])
            expiry = datetime.fromisoformat(envelope["valid_until"])
            valid_time = observed <= now < expiry <= observed + timedelta(seconds=TTL_SECONDS)
        except (KeyError, ValueError, TypeError):
            valid_time = False
        if (
            capability not in CAPABILITIES
            or envelope.get("source") != f"tool:{capability}"
            or envelope.get("spec_hash") != _spec_hash(normalized, capability, owner_email)
        ):
            for pid in ids or [None]:
                fail(pid, "INVALID_EVIDENCE", "工具来源不匹配", [capability] if capability else [])
            continue
        if not valid_time:
            for pid in ids or [None]:
                fail(pid, "EVIDENCE_EXPIRED", "工具证据过期或时间无效", [capability])
            continue
        for item in envelope.get("items", []):
            pid = item.get("product_id")
            key = (capability, pid)
            if key in evidence_by_key and evidence_by_key[key] != item:
                fail(pid, "INVALID_EVIDENCE", "同一能力存在冲突回执", [capability])
            evidence_by_key[key] = item
            if capability == "search_candidates":
                if item.get("id") != pid or item.get("source_ids") != {"products": [pid]}:
                    fail(pid, "INVALID_EVIDENCE", "检索商品来源行 ID 不一致", [capability])
                    continue
                candidate_ids.add(pid)

    seen = set()
    verified = []
    async with pool.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
        if not await conn.fetchval("SELECT 1 FROM users WHERE email=$1 AND is_active", owner_email):
            fail(None, "UNKNOWN_OWNER", "申请人不存在或已停用")
        for option in options:
            pid = option.get("product_id") if isinstance(option, dict) else None
            if pid in seen or pid not in candidate_ids:
                fail(pid, "INVALID_EVIDENCE", "商品未被上游候选检索核验或重复")
                continue
            seen.add(pid)
            try:
                product = (await _products(conn, _ids([pid])))[0]
            except DomainError as exc:
                fail(pid, exc.code, str(exc))
                continue
            if type(option.get("quantity")) is not int or option.get("quantity") != normalized["quantity"]:
                fail(pid, "INVALID_QUANTITY", "备选方案数量与确认需求不符")
            tsq = or_joined_tsquery("$2")
            matches_query = await conn.fetchval(
                f"""SELECT 1 FROM products p WHERE id=$1
                    AND (p.search_vector @@ {tsq} OR p.name ILIKE $3 OR p.description ILIKE $3)""",
                product["id"],
                expand_catalog_query(normalized["query"]),
                "%" + normalized["query"] + "%",
            )
            if not matches_query:
                fail(pid, "INVALID_EVIDENCE", "商品不匹配当前确认的检索需求", ["search_candidates"])
            if option.get("name") != product["name"]:
                fail(pid, "INVALID_EVIDENCE", "商品名称与实时来源不符")
            if normalized["min_rating"] is not None and _money(product["rating"] or 0) < _money(
                normalized["min_rating"]
            ):
                fail(pid, "RATING_TOO_LOW", "商品评分不满足最低评分")
            fresh = {}
            for capability, field in (
                ("quote_options", "quote"),
                ("check_inventory", "inventory"),
                ("review_evidence", "review"),
            ):
                recorded = evidence_by_key.get((capability, pid))
                if recorded is None or option.get(field) != recorded:
                    fail(pid, "INVALID_EVIDENCE", "方案缺少相匹配的可信工具回执", [capability])
                    continue
                current = (await _execute(conn, capability, normalized, {"product_ids": [pid]}, owner_email, now))[
                    "items"
                ][0]
                fresh[field] = current
                if capability == "quote_options":
                    if recorded != current:
                        code = (
                            "PRICE_CHANGED" if recorded.get("unit_price") != current["unit_price"] else "COUPON_CHANGED"
                        )
                        fail(pid, code, "报价或优惠规则已变化，需重新取得证据", [capability])
                    if _money(current["final_total"]) > normalized["total_budget"]:
                        fail(pid, "BUDGET_EXCEEDED", "单一备选商品总价超过预算", [capability])
                elif capability == "check_inventory":
                    if not current["sufficient"]:
                        fail(pid, "INSUFFICIENT_STOCK", "实时库存不足以满足采购数量", [capability])
                    elif recorded != current:
                        fail(pid, "INVENTORY_CHANGED", "库存证据已变化", [capability])
                else:
                    if recorded != current:
                        fail(pid, "REVIEWS_CHANGED", "评论原文或风险引用已变化", [capability])
                    if current["evidence_status"] != "available":
                        fail(pid, "INSUFFICIENT_REVIEWS", "没有可核实的评论，不能宣称风险分析充分", [capability])
            verified.append({"product_id": pid, "name": product["name"], "quantity": normalized["quantity"], **fresh})
    return {
        "passed": not failures,
        "verified_at": now.isoformat(),
        "options": verified,
        "failures": failures,
        "affected_product_ids": sorted({f["product_id"] for f in failures if f["product_id"]}),
        "scope": "CNY商品金额、库存与原始评论；不含运费、购买/占库、主观偏好满足或生产收益",
    }
