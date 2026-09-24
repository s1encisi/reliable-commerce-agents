"""MAF v1 —— 第 20b 章：DevUI 快速上手。

把一个智能体注册到 DevUI 的 serve() 辅助函数，并在 localhost:8090 启动
浏览器面板。DevUI 是仅限开发、仅限 Python 的调试台，暴露 OpenAI 兼容的
Responses API 以及一个实时追踪面板。

运行：
    uv run python main.py
然后打开 http://localhost:8090
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap

maf_bootstrap.bootstrap()

from agent_framework import Agent
from agent_framework.devui import serve
from agent_framework.openai import (
    OpenAIChatClient,
    OpenAIChatCompletionClient,
)


def _client():
    """依据 LLM_PROVIDER 选择聊天客户端，与本系列的约定保持一致。"""
    if os.environ.get("LLM_PROVIDER", "openai").lower() == "azure":
        return OpenAIChatCompletionClient(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    return OpenAIChatClient(
        model=os.environ.get("LLM_MODEL", "gpt-4.1"),
        api_key=os.environ["OPENAI_API_KEY"],
        # 第 9 阶段：可用任意 OpenAI 兼容端点（GitHub Models、OpenRouter、
        # vLLM、LM Studio、Ollama）替代 api.openai.com——见
        # tutorials/00-setup/README.md 的「没有付费 API 密钥怎么办？」一节。
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )


def build_agent() -> Agent:
    """单个演示智能体——DevUI 会以 id 'devui-demo' 注册它。"""
    return Agent(
        _client(),
        instructions="你是一个友好的演示商店电商助手。",
        name="devui-demo",
        description="注册到 MAF DevUI 的演示智能体",
    )


if __name__ == "__main__":
    # DevUI 会在 http://localhost:8090 打开浏览器，并把每次运行的
    # OpenTelemetry 跨度流入它的追踪标签页。
    serve(
        entities=[build_agent()],
        port=8090,
        auto_open=True,
        instrumentation_enabled=True,
    )
