"""模型输出内容审核中间件。

检查模型自身生成文本，独立于工具输出净化和输入注入检测。非流式
响应直接检查 context.result；流式响应在 stream_result_hooks 中等待
最终文本。此时分块已发送，无法撤回，具体标记和替换行为以分支实现
为准。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework import ChatResponse, Message, ResponseStream
from agent_framework._middleware import ChatContext, ChatMiddleware

from shared.config import settings
from shared.guardrails.moderation import classify

logger = logging.getLogger(__name__)

REFUSAL_MESSAGE = (
    "I'm not able to share that response — it was flagged by content moderation. "
    "If this seems like a mistake, please rephrase your question."
)


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for message in getattr(response, "messages", None) or []:
        for content in getattr(message, "contents", None) or []:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(content, str):
                parts.append(content)
    return "\n".join(parts)


class OutputModerationMiddleware(ChatMiddleware):
    """对最终回答分类，并按配置选择是否阻止。"""

    def __init__(self) -> None:
        self.flagged = 0

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        mode = settings.OUTPUT_MODERATION_MODE
        if mode == "off":
            await call_next()
            return

        await call_next()

        if context.result is None:
            return
        if context.stream and isinstance(context.result, ResponseStream):
            context.stream_result_hooks.append(lambda response: self._check(context, response))
        elif not context.stream:
            checked = self._check(context, context.result)
            if checked is not None:
                context.result = checked

    def _check(self, context: ChatContext, response: Any) -> Any:
        text = _response_text(response)
        if not text:
            return response

        categories = classify(text)
        if not categories:
            return response

        self.flagged += 1
        category_names = sorted(c.value for c in categories)
        logger.warning(
            "guardrails.output_moderation_flagged categories=%s mode=%s streaming=%s",
            category_names,
            settings.OUTPUT_MODERATION_MODE,
            bool(context.stream),
        )

        if settings.OUTPUT_MODERATION_MODE != "enforce":
            return response
        if context.stream:
            # 分块已发送，无法继续阻止或撤回，
            # 流式路径只保留前面的审核标记。
            return response

        return self._refusal_result()

    @staticmethod
    def _refusal_result() -> ChatResponse:
        return ChatResponse(
            messages=[Message(role="assistant", contents=[REFUSAL_MESSAGE])],
            finish_reason="content_filter",
        )
