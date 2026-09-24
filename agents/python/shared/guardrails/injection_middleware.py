"""输入提示注入检测中间件。

扫描高精度注入信号，记录计数、元数据和日志。默认只观察，由提示词
拒绝规则与工具输出净化共同防护。开启 GUARDRAILS_BLOCK_ON_INJECTION
后直接返回拒绝结果，不调用 call_next()，消息不会到达模型。

触发标记同时写入调用局部 metadata 和请求级 current_guardrail_flags；
后者供运行结束后的安全评测读取。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from agent_framework import ChatResponse, ChatResponseUpdate, Content, Message, ResponseStream
from agent_framework._middleware import ChatContext, ChatMiddleware

from shared.config import settings
from shared.guardrails.flags import current_guardrail_flags
from shared.guardrails.sanitize import contains_injection_markers

logger = logging.getLogger(__name__)

REFUSAL_MESSAGE = (
    "I can't process that request — it looks like it contains an attempt to override "
    "my instructions. If you have a genuine question, please rephrase it without the "
    "embedded commands."
)


class InjectionDetectionChatMiddleware(ChatMiddleware):
    """标记输入中的提示注入信号，并按配置选择是否阻止。"""

    def __init__(self) -> None:
        self.detections = 0

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        if not settings.GUARDRAILS_ENABLED:
            await call_next()
            return

        if self._flagged(context):
            self.detections += 1
            meta = getattr(context, "metadata", None)
            if isinstance(meta, dict):
                meta["guardrail_injection_detected"] = True
            flags = current_guardrail_flags.get()
            if flags is not None:
                flags["injection_detected"] = True

            if settings.GUARDRAILS_BLOCK_ON_INJECTION:
                logger.warning("guardrails.injection_blocked blocking=True")
                if flags is not None:
                    flags["injection_blocked"] = True
                context.result = self._refusal_result(context)
                # 直接短路，不调用 call_next()，
                # 被标记消息不会进入聊天客户端或模型。
                return

            logger.info("guardrails.injection_detected blocking=False")

        await call_next()

    @staticmethod
    def _flagged(context: ChatContext) -> bool:
        for message in getattr(context, "messages", None) or []:
            for content in getattr(message, "contents", None) or []:
                text = getattr(content, "text", None)
                if isinstance(text, str) and contains_injection_markers(text):
                    return True
        return False

    @staticmethod
    def _refusal_result(context: ChatContext) -> ChatResponse | ResponseStream:
        """构造与流式或非流式调用形态一致的拒绝响应。"""
        if getattr(context, "stream", False):

            async def _refusal_stream():
                yield ChatResponseUpdate(
                    role="assistant",
                    contents=[Content.from_text(text=REFUSAL_MESSAGE)],
                )

            return ResponseStream(_refusal_stream())

        return ChatResponse(
            messages=[Message(role="assistant", contents=[REFUSAL_MESSAGE])],
            finish_reason="content_filter",
        )
