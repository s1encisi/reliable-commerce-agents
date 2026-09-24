"""教程启动辅助函数。

各章导入时调用 bootstrap：按需修复早期 MAF 空 __init__.py 的
公开导出，并加载仓库 .env。当前已修复版本通常无需补丁；显式传入
的环境变量优先。
"""

from __future__ import annotations

import importlib
import os
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

_PATCH_MARKER = "# maf-v1-bootstrap-patch-v2"
_PATCH = f'''\
{_PATCH_MARKER}
"""Microsoft Agent Framework — re-exports for MAF v1 tutorials."""
__version__ = "1.0.0"

from agent_framework._agents import Agent, RawAgent, BaseAgent, SupportsAgentRun
from agent_framework._tools import tool, FunctionTool
from agent_framework._types import (
    Message,
    Content,
    Role,
    AgentResponse,
    AgentResponseUpdate,
    ChatResponse,
    ChatResponseUpdate,
    ResponseStream,
)
from agent_framework._clients import BaseChatClient
from agent_framework._sessions import (
    AgentSession,
    HistoryProvider,
    InMemoryHistoryProvider,
    ContextProvider,
)
'''


def _patch_init() -> None:
    import agent_framework

    init_path = pathlib.Path(agent_framework.__file__)
    current = init_path.read_text()
    # 只在文件为空，或含旧引导补丁时处理，
    # 用当前导出清单替换旧补丁。
    if current.strip() == "" or (_PATCH_MARKER not in current and "Microsoft Agent Framework — re-exports" in current):
        init_path.write_text(_PATCH)
        importlib.reload(agent_framework)


def _load_dotenv() -> None:
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # 不覆盖调用方显式设置的环境变量。
        os.environ.setdefault(key, value)


def bootstrap() -> None:
    """幂等初始化，可安全调用多次。"""
    _patch_init()
    _load_dotenv()
