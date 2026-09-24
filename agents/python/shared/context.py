"""通过 ContextVar 保存请求级状态，由认证中间件写入、工具读取。"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

current_user_email: ContextVar[str] = ContextVar("current_user_email", default="")
current_user_role: ContextVar[str] = ContextVar("current_user_role", default="")
current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")

# 请求级执行步骤；请求外为 None，中间件不记录。
# 每次启用记录的请求都会创建新列表。
current_steps: ContextVar[list | None] = ContextVar("current_steps", default=None)

# SSE 桥接队列由编排器在智能体运行前设置，
# call_specialist_agent 将专业智能体的响应分块写入队列，
# 供编排器即时转发给浏览器。
# 非流式请求和测试中通常为 None。
current_stream_queue: ContextVar[asyncio.Queue | None] = ContextVar("current_stream_queue", default=None)
