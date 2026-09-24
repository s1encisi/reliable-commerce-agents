"""把远程 A2A 专业智能体包装成 MAF 聊天客户端。

HandoffBuilder 需要 Agent 参与者，而专业智能体运行在独立微服务。
此 BaseChatClient 子类向 /message:send 发送会话，把回复转换为
ChatResponse，因此可由 MAF 原生交接机制调用。
"""

import logging
import uuid
from typing import Any

import httpx
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
)

from shared.http_resilience import ResilientAsyncTransport
from shared.oauth.service_client import build_a2a_headers

logger = logging.getLogger(__name__)

# 所有远程客户端共享传输实例。
# 熔断器需要跨调用保留失败记录，
# 每次新建实例会重置统计，
# 无法起到保护作用。共享实例按主机与端口
# 分别维护各专业智能体状态，
# 与 orchestrator/agent.py 中的 _A2A_TRANSPORT
# 采用相同设计。
_A2A_TRANSPORT = ResilientAsyncTransport()


class RemoteSpecialistChatClient(BaseChatClient):
    """将生成请求委托给 A2A 专业智能体的 BaseChatClient。

    非流式调用发送一次 /message:send，包装为助手消息；流式接口也只
    输出包含完整回复的一个更新。需要真正 SSE 分块时，应直接使用
    专业智能体的流式端点。
    """

    OTEL_PROVIDER_NAME = "a2a-remote"

    def __init__(self, *, name: str, url: str, timeout: float = 30.0) -> None:
        super().__init__()
        self._agent_name = name
        self._url = url.rstrip("/")
        self._timeout = timeout

    async def _post(self, prompt: str) -> str:
        headers = await build_a2a_headers()
        async with httpx.AsyncClient(timeout=self._timeout, transport=_A2A_TRANSPORT) as client:
            resp = await client.post(
                f"{self._url}/message:send",
                json={"message": prompt, "history": []},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("response", resp.text))

    @staticmethod
    def _prompt_from_messages(messages) -> str:
        """把 MAF 消息列表展平为一条 A2A 提示字符串。"""
        parts: list[str] = []
        for msg in messages or []:
            text = getattr(msg, "text", None)
            if text:
                parts.append(str(text))
        return "\n\n".join(parts)

    def _inner_get_response(
        self,
        *,
        messages,
        stream: bool,
        options: Any = None,
        **_: Any,
    ):
        """按 stream 标志返回响应协程或 ResponseStream。

        MAF 快速路径要求此方法同步返回相应对象，因此实际 I/O 放在内部
        异步函数中，而不是把整个方法改为异步。
        """
        prompt = self._prompt_from_messages(messages)

        if stream:
            agent_name = self._agent_name

            async def _gen():
                reply = await self._post(prompt)
                yield ChatResponseUpdate(
                    role="assistant",
                    contents=[Content.from_text(text=reply)],
                    author_name=agent_name,
                )

            return ResponseStream(_gen())

        async def _respond() -> ChatResponse:
            reply = await self._post(prompt)
            return ChatResponse(
                messages=[
                    Message(
                        role="assistant",
                        contents=[reply],
                        author_name=self._agent_name,
                    )
                ],
                response_id=str(uuid.uuid4()),
                finish_reason="stop",
            )

        return _respond()


def make_remote_specialist_agent(name: str, url: str, *, description: str | None = None) -> Agent:
    """创建由 RemoteSpecialistChatClient 驱动的 Agent。

    可直接加入 HandoffBuilder.participants。这里只保留最小指令，
    远程专业智能体负责执行自己的系统提示词。
    """
    return Agent(
        client=RemoteSpecialistChatClient(name=name, url=url),
        name=name,
        description=description or f"Remote specialist {name} accessed over A2A.",
        instructions=f"You are the remote {name} specialist. Reply directly with the user's request.",
        # agent-framework-orchestrations>=1.0.1 的构建器要求设置此项。
        # 构建时会校验每个参与者，
        # 因为交接中间件会短路工具调用，
        # 否则本地历史可能与远程服务实际接收的内容不一致。
        require_per_service_call_history_persistence=True,
    )
