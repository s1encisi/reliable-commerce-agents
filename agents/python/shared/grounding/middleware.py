"""智能体级事实核验，按 GROUNDING_MODE 分派。

与函数级 GroundingLedgerMiddleware 一起挂载，检查最终文本中的
商品和订单卡片是否对应真实记录；仅校验 UUID 格式无法发现虚构标识。

流式执行使用 stream_result_hooks：MAF 在 ResponseStream 完全消费后
自动调用 get_final_response()，再执行核验。浏览器此前已收到原始分块，
enforce 只能修正持久化的最终结果及事实核验报告，不能撤回已展示内容。
必须保证用户不看到未核验卡片时，应使用非流式路径。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework import Content, ResponseStream
from agent_framework._middleware import AgentContext, AgentMiddleware

from shared.config import settings
from shared.grounding.extractor import extract_claims, rewrite_cards
from shared.grounding.ledger import current_grounding_ledger
from shared.grounding.verifier import ClaimVerdict, GroundingReport, verify_claims

logger = logging.getLogger(__name__)


class GroundingVerificationMiddleware(AgentMiddleware):
    """核验最终回答；enforce 模式下同时修正。"""

    def __init__(self) -> None:
        self.verified_total = 0
        self.unverified_total = 0

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        mode = settings.GROUNDING_MODE
        if mode == "off":
            await call_next()
            return

        await call_next()
        if context.result is None:
            return

        if context.stream and isinstance(context.result, ResponseStream):
            context.stream_result_hooks.append(lambda response: self._verify_and_apply(response, mode))
        elif not context.stream:
            context.result = await self._verify_and_apply(context.result, mode)

    async def _verify_and_apply(self, response: Any, mode: str) -> Any:
        text = getattr(response, "text", "") or ""
        if not text:
            return response

        claims = extract_claims(text)
        if claims.total_count == 0:
            return response

        pool = _get_pool_or_none()
        report = await verify_claims(claims, current_grounding_ledger.get(), pool)
        self.verified_total += report.verified_count
        self.unverified_total += report.unverified_count
        logger.info(
            "grounding.verified total=%d verified=%d unverified=%d mode=%s",
            report.total_count,
            report.verified_count,
            report.unverified_count,
            mode,
        )

        if mode in ("annotate", "enforce"):
            _attach_report(response, report)

        if mode == "enforce":
            corrected = _apply_corrections(text, report)
            if corrected != text:
                _rewrite_response_text(response, corrected)

        return response


def _get_pool_or_none() -> Any:
    from shared.db import get_pool

    try:
        return get_pool()
    except RuntimeError:
        return None


def _attach_report(response: Any, report: GroundingReport) -> None:
    props = getattr(response, "additional_properties", None)
    if not isinstance(props, dict):
        return
    props["grounding"] = {
        "total": report.total_count,
        "verified": report.verified_count,
        "unverified": report.unverified_count,
        "claims": [
            {
                "type": v.claim_type,
                "id": v.identifier,
                "status": v.status,
                "detail": v.detail,
                "source": v.source,
            }
            for v in report.verdicts
        ],
    }


def _apply_corrections(text: str, report: GroundingReport) -> str:
    product_verdicts = {v.identifier: v for v in report.verdicts if v.claim_type == "product"}
    order_verdicts = {v.identifier: v for v in report.verdicts if v.claim_type == "order"}

    def decide(entry: dict[str, Any], verdicts: dict[str, ClaimVerdict], price_key: str) -> dict[str, Any] | None:
        verdict = verdicts.get(str(entry.get("id")))
        if verdict is None:
            # 缺少标识或提取器跳过了非法条目时，
            # 保持原样，不猜测修正。
            return entry
        if verdict.status == "not_found":
            return None
        if verdict.status == "price_mismatch" and verdict.corrected_value is not None:
            entry = dict(entry)
            entry[price_key] = verdict.corrected_value
            return entry
        # verified 或暂时无法核验（如数据库不可达）的卡片，
        # 不能仅因检查未完成就删除。
        return entry

    return rewrite_cards(
        text,
        lambda entry: decide(entry, product_verdicts, "price"),
        lambda entry: decide(entry, order_verdicts, "total"),
    )


def _rewrite_response_text(response: Any, new_text: str) -> None:
    messages = getattr(response, "messages", None)
    if not messages:
        return
    last = messages[-1]
    contents = getattr(last, "contents", None)
    if not contents or not all(getattr(c, "type", None) == "text" for c in contents):
        logger.warning("grounding.enforce_skip reason=non_text_final_message")
        return
    last.contents = [Content.from_text(text=new_text)]
