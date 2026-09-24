"""
MAF v1 —— 第 23 章：A2A 协议（Python）

一个协调者智能体通过真实的完整项目所使用的同一套 A2A HTTP 形态调用一个
「订单查询」专业智能体：用 GET agent-card 获取身份、用阻塞式
POST /message:send，以及流式 POST /message:stream（SSE）。

这里的专业智能体是一个极小的 Starlette 应用 —— 不是 mock，而是带真实路由的
真实 ASGI 应用 —— 通过 httpx 的 ASGITransport 驱动，因此请求确实会走完
Starlette 的路由/JSON/SSE 机制，只是不打开真实的 TCP 套接字。为什么选择这种
取舍而不是拉起 `uvicorn`，见 README 的「为什么用进程内传输」小节。

运行：
    source agents/.venv/bin/activate
    python tutorials/23-a2a-protocol/python/main.py "What's the status of ORD-1001?"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys
from typing import Annotated

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

import httpx  # noqa: E402
from agent_framework import Agent, tool  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import Field  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, StreamingResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = (
    "You are a customer-support coordinator. "
    "When the user asks about the status of an order (they'll usually mention an order id "
    "like 'ORD-1001'), call the `call_order_specialist` tool with their question verbatim. "
    "For other questions, answer directly in one short sentence."
)
DEFAULT_QUESTION = "What's the status of order ORD-1001?"

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

# ─────────────────────────────────────────────────────────────────
# 「远端」一侧：一个订单查询专业智能体，以 Starlette 应用的形式承载，
# 暴露真实的 A2A 接口 —— 与 agents/python/shared/agent_host.py
# ::create_agent_app() 为完整项目中每个专业智能体提供的同样三个端点：
#   GET  /.well-known/agent-card.json  —— 身份/发现
#   POST /message:send                 —— 阻塞式请求/响应
#   POST /message:stream                —— SSE 流式
# ─────────────────────────────────────────────────────────────────

AGENT_CARD = {
    "name": "order-lookup",
    "description": "Looks up order status by order id.",
    "url": "http://order-lookup.local",
    "version": "1.0",
}

# 预置数据，与第 02 章的天气字典同一思路 —— 本章的重点是传输，
# 而不是一个真实的订单数据库。
ORDERS: dict[str, str] = {
    "ord-1001": "Shipped, arriving 2026-08-22.",
    "ord-1002": "Processing — not yet shipped.",
    "ord-1003": "Delivered on 2026-08-15.",
}

_ORDER_ID_RE = re.compile(r"ORD-\d+", re.IGNORECASE)


def _lookup_order(message: str) -> str:
    """纯查询 —— 无 I/O。专业智能体的各端点包装的就是它。"""
    match = _ORDER_ID_RE.search(message)
    if not match:
        return "No order id found in the request. Expected something like 'ORD-1001'."
    order_id = match.group(0).lower()
    return ORDERS.get(order_id, f"No order found with id {match.group(0)}.")


async def _agent_card(request: Request) -> JSONResponse:
    del request
    return JSONResponse(AGENT_CARD)


async def _message_send(request: Request) -> JSONResponse:
    body = await request.json()
    message = body.get("message", "")
    if not message:
        return JSONResponse({"error": "No message provided"}, status_code=400)
    return JSONResponse({"response": _lookup_order(message), "steps": []})


async def _message_stream(request: Request) -> StreamingResponse:
    body = await request.json()
    message = body.get("message", "")

    async def _generate():
        if not message:
            yield "data: [ERROR: no message]\n\n"
            return
        # 真实的专业智能体是逐 token 流式输出的；本演示把整个回答作为一帧
        # SSE 发出，然后发出 agents/python/shared/agent_host.py::message_stream()
        # 所发出的同一个 "[DONE]" 哨兵 —— 这里重要的是帧的*形态*，
        # 而不是 token 粒度。
        yield f"data: {_lookup_order(message)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


def build_specialist_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/.well-known/agent-card.json", _agent_card, methods=["GET"]),
            Route("/message:send", _message_send, methods=["POST"]),
            Route("/message:stream", _message_stream, methods=["POST"]),
        ]
    )


SPECIALIST_APP = build_specialist_app()
SPECIALIST_BASE_URL = "http://order-lookup.local"


def _specialist_client() -> httpx.AsyncClient:
    # ASGITransport 在进程内驱动 Starlette 应用 —— 真实的 HTTP 请求/响应
    # 对象、真实的路由，但没有套接字。见 README。
    transport = httpx.ASGITransport(app=SPECIALIST_APP)
    return httpx.AsyncClient(transport=transport, base_url=SPECIALIST_BASE_URL, timeout=10)


async def demo_fetch_agent_card() -> dict:
    async with _specialist_client() as client:
        resp = await client.get("/.well-known/agent-card.json")
        resp.raise_for_status()
        return resp.json()


async def demo_stream_call(message: str) -> list[str]:
    """镜像 orchestrator/agent.py::call_specialist_agent 中的 SSE 解析：
    读取 `data: ` 行，在 `[DONE]` 哨兵处停止，把 `[ERROR` 前缀当作
    失败帧而不是真实内容。
    """
    chunks: list[str] = []
    async with _specialist_client() as client:
        stream_ctx = client.stream("POST", "/message:stream", json={"message": message})
        async with stream_ctx as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: ") :]
                if payload == "[DONE]":
                    break
                if payload.startswith("[ERROR"):
                    raise RuntimeError(payload)
                chunks.append(payload)
    return chunks


# ─────────────────────────────────────────────────────────────────
# 「本地」一侧：一个协调者智能体，其唯一的工具就是一次 A2A 调用 ——
# 与 orchestrator/agent.py::call_specialist_agent 的阻塞路径同形：
# 组装请求体、POST /message:send、读取 `response`。
# ─────────────────────────────────────────────────────────────────


@tool(
    name="call_order_specialist",
    description="Call the order-lookup specialist over A2A to check an order's status. Pass the question verbatim.",
)
async def call_order_specialist(
    message: Annotated[str, Field(description="The order question to forward, e.g. 'What's the status of ORD-1001?'")],
) -> str:
    request_body = {"message": message}
    async with _specialist_client() as client:
        resp = await client.post("/message:send", json=request_body)
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("response", resp.text))


def _default_client() -> OpenAIChatClient | OpenAIChatCompletionClient | ReplayChatClient:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "replay":
        return ReplayChatClient(
            fixtures_dir=FIXTURES_DIR,
            record=os.environ.get("RECORD", "").lower() in ("1", "true", "yes"),
            record_provider=os.environ.get("REPLAY_RECORD_PROVIDER", "openai"),
        )
    if provider == "azure":
        return OpenAIChatCompletionClient(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    return OpenAIChatClient(
        model=os.environ.get("LLM_MODEL", "gpt-4.1"),
        api_key=os.environ["OPENAI_API_KEY"],
        # Phase 9：改用任何 OpenAI 兼容端点（GitHub Models、OpenRouter、
        # vLLM、LM Studio、Ollama），而不是 api.openai.com —— 见
        # tutorials/00-setup/README.md 的「没有付费 API 密钥？」小节。
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )


def build_agent(client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="coordinator",
        tools=[call_order_specialist],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    agent = build_agent()
    answer = await ask(agent, question)
    print(f"Q: {question}")
    print(f"A: {answer}")

    # 附加内容：直接演练两种原始 A2A 传输形态（不经过 LLM）—— 这正是
    # 协调者的工具以及真实 A2A 调用方在生产中对
    # agents/python/shared/agent_host.py 发起的同一批调用。
    card = await demo_fetch_agent_card()
    print(f"\nAgent card: {card}")
    chunks = await demo_stream_call(question)
    print(f"Streamed frames: {chunks}")


if __name__ == "__main__":
    asyncio.run(main())
