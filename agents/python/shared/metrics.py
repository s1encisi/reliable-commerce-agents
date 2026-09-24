"""应用自有的 OTel 费用与 token 指标。

CostBudgetMiddleware 已逐轮估算模型费用；这里把相同结果写入计数器，
供支持指标的 OTLP 后端统计和告警。Jaeger 本身只接收追踪，不能作为
本模块指标的存储端。

标签保持低基数，仅含模型、模式和智能体；不附加用户邮箱，避免
为每个用户创建时间序列或向遥测系统泄露身份。

费用由 token 数和手工价格表推算，不是账单；缺少用量时无法得到
有效估算，实际支出应与提供方账单核对。
"""

from __future__ import annotations

import logging
from typing import Any

from shared.config import settings

logger = logging.getLogger(__name__)

# 首次使用时创建指标，避免在导入阶段绑定 MeterProvider。
# 指标会绑定创建时的提供器，
# 而中间件导入往往早于遥测初始化。
# 过早绑定默认空操作提供器，
# 可能导致指标静默丢失。
_instruments: dict[str, Any] | None = None


def _get_instruments() -> dict[str, Any] | None:
    """按需初始化一次指标；遥测关闭时返回 None。"""
    global _instruments

    if not settings.OTEL_ENABLED:
        return None
    if _instruments is not None:
        return _instruments

    try:
        from shared.telemetry import get_meter

        meter = get_meter("ecommerce.cost")
        _instruments = {
            "cost": meter.create_counter(
                "ecommerce.llm.cost.usd",
                unit="USD",
                description="Estimated LLM spend, summed per turn from token usage.",
            ),
            "tokens": meter.create_counter(
                "ecommerce.llm.tokens",
                unit="{token}",
                description="LLM tokens consumed, split by direction.",
            ),
        }
    except Exception:
        # 遥测不能使业务请求失败。
        # 指标后端配置错误或不可达是运维问题，
        # 不能让客户问题因此报错。
        logger.warning("metrics.instrument_init_failed — cost metrics disabled", exc_info=True)
        _instruments = {}

    return _instruments


def record_llm_turn_cost(
    cost_usd: float,
    *,
    model: str,
    tokens_in: int,
    tokens_out: int,
    agent: str = "",
    mode: str = "",
) -> None:
    """记录一轮可计价的模型调用。

    费用和 token 数同时保存，便于区分支出变化来自用量变化，
    还是手工价格表变更。
    """
    instruments = _get_instruments()
    if not instruments:
        return

    attributes = {"model": model or "unknown"}
    if agent:
        attributes["agent"] = agent
    if mode:
        attributes["mode"] = mode

    try:
        instruments["cost"].add(cost_usd, attributes)
        instruments["tokens"].add(tokens_in, {**attributes, "direction": "input"})
        instruments["tokens"].add(tokens_out, {**attributes, "direction": "output"})
    except Exception:
        logger.warning("metrics.record_failed cost_usd=%.6f", cost_usd, exc_info=True)


def _reset_for_tests() -> None:
    """清空缓存指标，供测试安装独立的 MeterProvider。"""
    global _instruments
    _instruments = None
