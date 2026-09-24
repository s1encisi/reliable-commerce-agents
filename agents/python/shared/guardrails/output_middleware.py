"""工具输出净化中间件，防御存储型提示注入。

工具执行后，原地改写允许列表中含用户内容的 context.result，使评论、
商品描述或订单备注中的恶意指令作为普通数据返回。护栏关闭时不处理；
意外失败默认记录并返回原结果，GUARDRAILS_FAIL_OPEN=False 时重新抛错。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from agent_framework._middleware import FunctionInvocationContext, FunctionMiddleware

from shared.config import settings
from shared.function_results import rewrap_function_result, unwrap_function_result
from shared.guardrails.config import SANITIZE_TOOLS
from shared.guardrails.sanitize import neutralize_value

logger = logging.getLogger(__name__)


class OutputSanitizationMiddleware(FunctionMiddleware):
    """净化工具输出中的存储型或间接提示注入。"""

    def __init__(self, tools: dict[str, set[str] | None] | None = None) -> None:
        self.tools = tools if tools is not None else SANITIZE_TOOLS
        self.sanitized = 0

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        await call_next()

        if not (settings.GUARDRAILS_ENABLED and settings.GUARDRAILS_OUTPUT_SANITIZATION):
            return

        fn = getattr(context, "function", None)
        name = getattr(fn, "name", None) or getattr(fn, "__name__", None)
        if name not in self.tools:
            return

        original = getattr(context, "result", None)
        if original is None:
            return

        # MAF 将工具结果包装为 list[Content]，JSON 存在 text 中。
        # 需要先通过 shared/function_results.py 解包，
        # 否则 neutralize_value 无法识别 Content，
        # 会静默跳过真正需要净化的内容。
        unwrapped = unwrap_function_result(original)

        try:
            cleaned = neutralize_value(unwrapped, fields=self.tools[name])
        except Exception:
            logger.exception("guardrails.output_sanitize_failed tool=%s", name)
            if settings.GUARDRAILS_FAIL_OPEN:
                return
            raise

        if cleaned != unwrapped:
            self.sanitized += 1
            logger.info("guardrails.output_sanitized tool=%s", name)
        context.result = rewrap_function_result(original, cleaned)
