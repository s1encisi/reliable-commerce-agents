"""购前调研工作流 —— MAF 并发编排。

并行运行三个数据采集工具调用（评论、库存、价格
历史），将结果扇入到一个依赖库存的顺序式运费估算，
最后综合出最终建议。

已按 ``plans/refactor/08-pre-purchase-concurrent.md`` 从自定义的
``asyncio.gather`` 状态机重构为 MAF ``WorkflowBuilder``，使用
``add_fan_out_edges`` + ``add_fan_in_edges``。公共 API ——
类、dataclass 以及 ``execute(state) -> state`` 签名 —— 均保持不变，
调用方无需改动。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext

logger = logging.getLogger(__name__)


@dataclass
class ResearchState:
    """在各执行器之间传递工作流的进行中状态。"""

    product_id: str
    user_region: str = "east"

    # 由扇出执行器填充
    reviews: dict = field(default_factory=dict)
    stock: dict = field(default_factory=dict)
    price_history: dict = field(default_factory=dict)
    shipping: dict = field(default_factory=dict)

    # 最终输出
    recommendation: str = ""
    completed_steps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ─────────────────────── 执行器 ───────────────────────


class _FanOutExecutor(Executor):
    """起始节点 —— 将初始状态广播给三个采集器。"""

    def __init__(self) -> None:
        super().__init__(id="fan-out")

    @handler
    async def run(self, state: ResearchState, ctx: WorkflowContext[ResearchState]) -> None:
        await ctx.send_message(state)


class _ReviewsExecutor(Executor):
    """调用 analyze_sentiment 并把评论挂到状态上。"""

    def __init__(self, tools: dict[str, Any]) -> None:
        super().__init__(id="reviews")
        self._tools = tools

    @handler
    async def run(self, state: ResearchState, ctx: WorkflowContext[ResearchState]) -> None:
        fn = self._tools.get("analyze_sentiment")
        if fn is None:
            # 工具缺失过去是静默的空操作：没有报错、没有日志、completed_steps
            # 里也没有记录 —— 与"工具跑了但什么也没找到"完全无法区分。这个工作流
            # 就是这样在上线后只用了一半输入作答，表面上却看起来一切正常。
            state.errors.append("reviews: tool 'analyze_sentiment' is not registered")
            logger.warning("pre_purchase.tool_missing name=analyze_sentiment")
        else:
            try:
                state.reviews = await fn(product_id=state.product_id)
                state.completed_steps.append("reviews")
            except Exception as exc:
                state.errors.append(f"reviews: {exc}")
                logger.warning("pre_purchase.step_failed name=reviews error=%s", exc)
        await ctx.send_message(state)


class _StockExecutor(Executor):
    """调用 check_stock。"""

    def __init__(self, tools: dict[str, Any]) -> None:
        super().__init__(id="stock")
        self._tools = tools

    @handler
    async def run(self, state: ResearchState, ctx: WorkflowContext[ResearchState]) -> None:
        fn = self._tools.get("check_stock")
        if fn is None:
            state.errors.append("stock: tool 'check_stock' is not registered")
            logger.warning("pre_purchase.tool_missing name=check_stock")
        else:
            try:
                state.stock = await fn(product_id=state.product_id)
                state.completed_steps.append("stock")
            except Exception as exc:
                state.errors.append(f"stock: {exc}")
                logger.warning("pre_purchase.step_failed name=stock error=%s", exc)
        await ctx.send_message(state)


class _PriceHistoryExecutor(Executor):
    """调用 get_price_history，覆盖最近 90 天。"""

    def __init__(self, tools: dict[str, Any]) -> None:
        super().__init__(id="price-history")
        self._tools = tools

    @handler
    async def run(self, state: ResearchState, ctx: WorkflowContext[ResearchState]) -> None:
        fn = self._tools.get("get_price_history")
        if fn is None:
            state.errors.append("price_history: tool 'get_price_history' is not registered")
            logger.warning("pre_purchase.tool_missing name=get_price_history")
        else:
            try:
                state.price_history = await fn(product_id=state.product_id, days=90)
                state.completed_steps.append("price_history")
            except Exception as exc:
                state.errors.append(f"price_history: {exc}")
                logger.warning("pre_purchase.step_failed name=price_history error=%s", exc)
        await ctx.send_message(state)


class _MergeAndShipExecutor(Executor):
    """扇入屏障：合并三份并行的状态快照，并且
    如果库存已确认，则顺序执行运费估算。"""

    def __init__(self, tools: dict[str, Any]) -> None:
        super().__init__(id="merge-and-ship")
        self._tools = tools

    @handler
    async def run(
        self,
        inputs: list[ResearchState],
        ctx: WorkflowContext[ResearchState],
    ) -> None:
        merged = _merge_states(inputs)

        if not merged.stock.get("in_stock"):
            # 这不是错误：缺货商品没什么可估算运费的。
            # 之所以记录，是为了让读者能区分"我们没检查"与"我们检查了但什么也没找到"，
            # 因为建议文本本身无法区分这两种情况。
            merged.errors.append("shipping: skipped, product is out of stock")
        else:
            fn = self._tools.get("estimate_shipping")
            if fn is None:
                merged.errors.append("shipping: tool 'estimate_shipping' is not registered")
                logger.warning("pre_purchase.tool_missing name=estimate_shipping")
            else:
                try:
                    merged.shipping = await fn(
                        product_id=merged.product_id,
                        destination_region=merged.user_region,
                    )
                    merged.completed_steps.append("shipping")
                except Exception as exc:
                    merged.errors.append(f"shipping: {exc}")
                    logger.warning("pre_purchase.step_failed name=shipping error=%s", exc)

        await ctx.send_message(merged)


class _SynthesisExecutor(Executor):
    """终端节点 —— 生成建议字符串并输出。"""

    def __init__(self) -> None:
        super().__init__(id="synthesis")

    @handler
    async def run(
        self,
        state: ResearchState,
        ctx: WorkflowContext[None, ResearchState],
    ) -> None:
        state.recommendation = _build_recommendation(state)
        await ctx.yield_output(state)


# ─────────────────────── 辅助函数 ───────────────────────


def _merge_states(inputs: list[ResearchState]) -> ResearchState:
    """将三份部分 ResearchState 合并为一份。"""
    merged = ResearchState(product_id=inputs[0].product_id, user_region=inputs[0].user_region)
    for partial in inputs:
        if partial.reviews:
            merged.reviews = partial.reviews
        if partial.stock:
            merged.stock = partial.stock
        if partial.price_history:
            merged.price_history = partial.price_history
        for step in partial.completed_steps:
            if step not in merged.completed_steps:
                merged.completed_steps.append(step)
        merged.errors.extend(e for e in partial.errors if e not in merged.errors)
    return merged


def _build_recommendation(state: ResearchState) -> str:
    parts: list[str] = []

    # 键名来自 TOOLS，而 TOOLS 才是契约。这段代码过去读的是
    # `sentiment` 和 `total_reviews`；而 analyze_sentiment 返回的是
    # `overall_sentiment` 和 `average_rating`。由于这里每一行都有
    # 守卫条件，这种不匹配没有产生任何错误 —— 只是永久性地少了一行，
    # 每一次运行都如此，从工作流写下那天起就是。
    if state.reviews.get("overall_sentiment"):
        rating = state.reviews.get("average_rating")
        detail = f" ({rating}/5 avg)" if rating else ""
        parts.append(f"Reviews: {state.reviews['overall_sentiment']}{detail}")

    if state.stock.get("in_stock"):
        parts.append(f"Stock: {state.stock.get('total_quantity', 0)} units available")
    else:
        parts.append("Stock: Currently out of stock")

    if state.price_history.get("is_good_deal"):
        parts.append(f"Price: Good deal (below {state.price_history.get('average_price', 0):.0f} avg)")
    elif state.price_history.get("trend"):
        parts.append(f"Price trend: {state.price_history['trend']}")

    # 同上：estimate_shipping 返回的是 `shipping_options`，而不是 `options`，
    # 并且每一项携带的是 `delivery_window` 而不是 `days`。
    if state.shipping.get("shipping_options"):
        cheapest = min(
            state.shipping["shipping_options"],
            key=lambda o: o.get("price", float("inf")),
        )
        window = cheapest.get("delivery_window", "timing unknown")
        parts.append(f"Shipping: from ${cheapest.get('price', 0):.2f}, {window}")

    if not parts:
        return "Insufficient data for recommendation"

    recommendation = " | ".join(parts)

    # 说明哪些内容没能检查。
    #
    # 上面每一行都以其数据存在作为守卫条件，这本身没错 —— 但这意味着
    # 一个失败的探测和一个什么都没找到的探测产出相同的输出：沉默。这个
    # 工作流曾经在四路扇出的情况下返回
    # "Stock: 348 units available | Price trend: stable"，而答案中没有任何
    # 地方说明另外两路根本没有运行。
    #
    # 一个承认缺失内容的简短答案是诚实的。悄悄省略它的答案才是真正对
    # 用户造成伤害的，因为它读起来像是一幅完整的图景。
    # 这里对照的是 DATA，而不是 completed_steps。一个探测可以执行到完成，
    # 却仍然返回不可用的东西 —— 一个空 dict，或者缺少上面那行所需唯一键
    # 的载荷 —— 而 `completed_steps` 只记录了它执行过。以此为依据来给出
    # 提示，会把最初的缺陷以一种更隐蔽的形式重现：四个步骤全都"完成"，
    # 两个什么都没贡献，而答案仍然是自信的 48 个字符，没有任何提示。
    contributed = {
        "reviews": bool(state.reviews.get("overall_sentiment")),
        "stock": bool(state.stock),
        "price_history": bool(state.price_history.get("trend") or state.price_history.get("is_good_deal")),
        "shipping": bool(state.shipping.get("shipping_options")),
    }
    missing = [name for name, ok in contributed.items() if not ok]
    if missing:
        recommendation += f" | (could not check: {', '.join(missing)})"

    return recommendation


# ─────────────────────── 公共 API ───────────────────────


class PrePurchaseWorkflow:
    """基于 MAF 的并行调研工作流。

    用 tools 字典构造一次，之后可以按需多次调用 ``execute(state)``；
    每次调用在内部都会构建一个全新的 MAF 工作流。
    """

    def __init__(self, tools: dict[str, Any]) -> None:
        self._tools = tools

    def _build_maf_workflow(self):
        fan_out = _FanOutExecutor()
        reviews = _ReviewsExecutor(self._tools)
        stock = _StockExecutor(self._tools)
        price = _PriceHistoryExecutor(self._tools)
        merge = _MergeAndShipExecutor(self._tools)
        synthesis = _SynthesisExecutor()

        return (
            WorkflowBuilder(start_executor=fan_out, name="pre-purchase")
            .add_fan_out_edges(fan_out, [reviews, stock, price])
            .add_fan_in_edges([reviews, stock, price], merge)
            .add_edge(merge, synthesis)
            .build()
        )

    async def execute(self, state: ResearchState) -> ResearchState:
        """运行工作流并返回最终填充完毕的状态。"""
        workflow = self._build_maf_workflow()

        final_state = state
        async for event in workflow.run(state, stream=True):
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, ResearchState):
                    final_state = data
        return final_state
