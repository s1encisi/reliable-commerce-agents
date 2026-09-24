"""本轮工具结果的结构化事实台账。

沿用请求级 ContextVar 与函数中间件模式：工具调用后追加事实，
未启用记录时不做处理。工具结构不同，商品用 id、订单用 order_id，
库存由 check_stock 返回 product_id、in_stock、total_quantity；
因此按字段组合识别，不能假设所有结果共用同一个标识或 stock 字段。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# 直接导入具体子模块，与 agent_observability.py 一致。
# 这样兼容早期顶层 __init__.py 为空的框架包。
from agent_framework._middleware import FunctionInvocationContext, FunctionMiddleware

from shared.function_results import unwrap_function_result


@dataclass(frozen=True)
class ProductFact:
    id: str
    name: str | None = None
    price: float | None = None
    image_url: str | None = None


@dataclass(frozen=True)
class OrderFact:
    id: str
    status: str | None = None
    total: float | None = None
    tracking: str | None = None


@dataclass(frozen=True)
class PromoFact:
    code: str
    discount_amount: float | None = None
    discount_type: str | None = None


@dataclass
class GroundingLedger:
    """本轮真实工具结果中的事实，以标识或代码为键保存。"""

    products: dict[str, ProductFact] = field(default_factory=dict)
    orders: dict[str, OrderFact] = field(default_factory=dict)
    promos: dict[str, PromoFact] = field(default_factory=dict)
    # 补充记录不符合商品、订单或促销结构的金额。
    # 例如 get_price_history 的 history 列表，
    # 或其他嵌套结构中的 price、amount，
    # 都需要递归收集。否则真实工具返回的
    # 历史价格也会被误记为 unverifiable，
    # 无法体现已有证据。
    known_amounts: set[float] = field(default_factory=set)

    def record(self, result: Any) -> None:
        for item in _iter_dicts(result):
            self._record_one(item)
            for value in item.values():
                if isinstance(value, list):
                    for nested in value:
                        if isinstance(nested, dict):
                            self._record_one(nested)
                            self._record_bare_amount(nested)
            self._record_bare_amount(item)

    def _record_bare_amount(self, item: dict[str, Any]) -> None:
        for key in (
            "price",
            "current_price",
            "average_price",
            "min_price",
            "max_price",
            "amount",
            "total",
            "discount_amount",
            # original_price 是商品详情和比较工具返回的真实列。
            # 回答可能同时引用现价和原价，
            # 两者都来自工具，
            # 不能把原价误标为无法核验。
            # 全文检索更常命中打折商品后，
            # 这个遗漏会更容易暴露。
            "original_price",
        ):
            value = _as_float(item.get(key))
            if value is not None:
                self.known_amounts.add(value)

    def _record_one(self, item: dict[str, Any]) -> None:
        if _looks_like_product(item):
            fact = ProductFact(
                id=str(item["id"]),
                name=item.get("name"),
                price=_as_float(item.get("price")),
                image_url=item.get("image_url"),
            )
            self.products[fact.id] = fact
        elif _looks_like_order(item):
            order_id = item.get("order_id") or item.get("id")
            if order_id is None:
                return
            fact = OrderFact(
                id=str(order_id),
                status=item.get("status"),
                total=_as_float(item.get("total")),
                tracking=item.get("tracking_number") or item.get("tracking"),
            )
            self.orders[fact.id] = fact
        elif _looks_like_promo(item):
            fact = PromoFact(
                code=str(item["code"]).upper(),
                discount_amount=_as_float(item.get("discount_amount")),
                discount_type=item.get("discount_type"),
            )
            self.promos[fact.code] = fact


def _iter_dicts(result: Any):
    if isinstance(result, dict):
        yield result
    elif isinstance(result, list):
        for entry in result:
            if isinstance(entry, dict):
                yield entry


def _looks_like_product(item: dict[str, Any]) -> bool:
    # 商品搜索、详情、比较、语义检索、
    # 相似商品和热门商品工具都在顶层返回 id、price、name。
    # 订单明细使用 unit_price，
    # 因此不会与此形态混淆。
    return "id" in item and "price" in item and "name" in item and "order_id" not in item


def _looks_like_order(item: dict[str, Any]) -> bool:
    # 订单详情包含 order_id、status、total、items。
    return "status" in item and "total" in item and ("order_id" in item or "items" in item)


def _looks_like_promo(item: dict[str, Any]) -> bool:
    # 优惠券校验成功时包含 valid=True、code、discount_amount 等字段。
    return "code" in item and "discount_amount" in item and item.get("valid") is True


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# 未启用事实记录时 ContextVar 为 None，
# 因此可以无条件挂载记录中间件。
current_grounding_ledger: ContextVar[GroundingLedger | None] = ContextVar("current_grounding_ledger", default=None)


class GroundingLedgerMiddleware(FunctionMiddleware):
    """将每次工具返回的商品、订单和促销事实记入台账。"""

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        await call_next()
        ledger = current_grounding_ledger.get()
        if ledger is None:
            return
        ledger.record(unwrap_function_result(getattr(context, "result", None)))


# 无状态实例，所有智能体共享，方式与 STEP_MIDDLEWARE 相同。
GROUNDING_LEDGER_MIDDLEWARE: list[FunctionMiddleware] = [GroundingLedgerMiddleware()]


def reset_grounding_ledger() -> GroundingLedger:
    """为当前请求或进程调用创建新台账。

    与 reset_steps() 一同调用：编排器每次请求重置，专业智能体每次
    独立进程调用也重置，各自只记录本轮实际执行的工具结果。
    """
    fresh = GroundingLedger()
    current_grounding_ledger.set(fresh)
    return fresh
