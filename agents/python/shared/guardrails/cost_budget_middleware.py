"""单次运行费用预算中间件，消费 shared/cost.py 的估算结果。

每轮模型调用结束后累计费用。off 不挂载；observe 只记录；enforce
在累计值超限后拒绝下一轮调用，不调用 call_next()。已经发出的调用
不会中途取消，因费用只能从完成后的 usage_details 得到，上限可能
被最后一轮超出。

current_run_cost_usd 使用 ContextVar 按异步任务隔离。未设置时首轮
以 0.0 开始累计；每个请求任务持有独立上下文快照。reset_run_cost()
提供显式清零边界，但中间件不依赖外部先调用它。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from agent_framework import ChatResponse, ChatResponseUpdate, Content, Message, ResponseStream
from agent_framework._middleware import ChatContext, ChatMiddleware

from shared.config import settings
from shared.cost import estimate_cost
from shared.metrics import record_llm_turn_cost

logger = logging.getLogger(__name__)

current_run_cost_usd: ContextVar[float | None] = ContextVar("current_run_cost_usd", default=None)


def reset_run_cost() -> float:
    """为当前请求或运行初始化费用累计，返回 0.0。"""
    current_run_cost_usd.set(0.0)
    return 0.0


def get_run_cost() -> float:
    """返回当前运行上下文中累计的估算费用。"""
    return current_run_cost_usd.get() or 0.0


def _add_run_cost(amount: float) -> float:
    """累加 amount，返回新的费用总额。"""
    total = get_run_cost() + amount
    current_run_cost_usd.set(total)
    return total


def _current_model() -> str:
    """按评测器相同规则解析模型或部署名。

    在此独立实现，避免 shared 反向依赖 evals。
    """
    if settings.LLM_PROVIDER.lower() == "azure":
        return settings.AZURE_OPENAI_DEPLOYMENT
    return settings.LLM_MODEL


def _turn_cost(response: Any) -> tuple[float, int, int] | None:
    """根据 ChatResponse.usage_details 估算本轮美元费用并返回 token 数。

    用量缺失时返回 None，区分无数据与免费调用。保留原始 token 数，
    便于判断费用变化来自实际用量还是手工维护的价格表。
    """
    usage = getattr(response, "usage_details", None)
    if not usage:
        return None
    tokens_in = usage.get("input_token_count") or 0
    tokens_out = usage.get("output_token_count") or 0
    cost = estimate_cost(_current_model(), tokens_in, tokens_out)
    if cost is None:
        return None
    return cost, tokens_in, tokens_out


BUDGET_REFUSAL_MESSAGE = (
    "This run has been stopped because it exceeded its configured cost budget. "
    "Start a new request, or raise COST_BUDGET_USD_PER_RUN if this ceiling is too low."
)


class CostBudgetMiddleware(ChatMiddleware):
    """累计运行费用，并在 enforce 模式限制后续调用。

    COST_BUDGET_MODE 不为 off 时，在 build_specialist_middleware 中
    与注入检测、个人信息脱敏等聊天层中间件一起挂载。
    """

    def __init__(self) -> None:
        self.turns_recorded = 0
        self.blocked = 0

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        mode = settings.COST_BUDGET_MODE
        if mode == "off":
            await call_next()
            return

        budget = settings.COST_BUDGET_USD_PER_RUN
        if mode == "enforce" and budget is not None and get_run_cost() > budget:
            self.blocked += 1
            logger.warning(
                "cost_budget.blocked run_cost_usd=%.4f budget_usd=%.4f",
                get_run_cost(),
                budget,
            )
            context.result = self._refusal_result(context)
            # 直接返回，不调用 call_next()；
            # 运行已超预算时不再发起下一轮模型调用。
            return

        await call_next()

        if context.result is None:
            return
        if context.stream and isinstance(context.result, ResponseStream):
            context.stream_result_hooks.append(self._record_from_response)
        elif not context.stream:
            self._record_from_response(context.result)

    def _record_from_response(self, response: Any) -> Any:
        priced = _turn_cost(response)
        if priced is not None:
            cost, tokens_in, tokens_out = priced
            self.turns_recorded += 1
            total = _add_run_cost(cost)
            logger.info(
                "cost_budget.turn_recorded turn_cost_usd=%.4f run_cost_usd=%.4f mode=%s",
                cost,
                total,
                settings.COST_BUDGET_MODE,
            )
            # 将同一费用估算同时写入计数器，
            # 避免告警只能依赖日志采集与解析。
            # 这里覆盖每一轮模型调用，
            # 包括专业智能体自身的调用。
            # 编排器 usage_logs 只有运行级汇总，
            # 不能单独说明费用分布。
            record_llm_turn_cost(
                cost,
                model=_current_model(),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                agent=settings.OTEL_SERVICE_NAME,
                mode=settings.COST_BUDGET_MODE,
            )
        return response

    @staticmethod
    def _refusal_result(context: ChatContext) -> ChatResponse | ResponseStream:
        """构造与流式或非流式调用形态一致的拒绝结果。

        方式与注入检测中间件一致。
        """
        if getattr(context, "stream", False):

            async def _refusal_stream():
                yield ChatResponseUpdate(
                    role="assistant",
                    contents=[Content.from_text(text=BUDGET_REFUSAL_MESSAGE)],
                )

            return ResponseStream(_refusal_stream())

        return ChatResponse(
            messages=[Message(role="assistant", contents=[BUDGET_REFUSAL_MESSAGE])],
            finish_reason="length",
        )
