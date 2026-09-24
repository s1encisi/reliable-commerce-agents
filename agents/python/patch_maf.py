"""仅在 agent_framework/__init__.py 为空时修复 MAF 1.0 打包缺陷。

当前锁定的 1.14.0 已提供完整的公开导出，因此正常安装上 patch() 不执行
写入。本脚本保留为兼容性兜底，详见 CLAUDE.md 的 MAF 包补丁说明。

写入必须使用临时文件和 os.replace()，不能原地 write_text()。Linux 下
uv 默认将安装文件硬链接到共享缓存；原地写入会修改同一 inode，污染
本机其他项目后续 uv sync 使用的缓存。os.replace() 创建新的目录项，
可保留缓存中的原始文件。
"""

import importlib
import os
import pathlib
import tempfile

PATCH = '''\
"""Microsoft Agent Framework — re-exports for E-Commerce Agents."""
__version__ = "1.0.0"

from agent_framework._agents import Agent, RawAgent, BaseAgent
from agent_framework._tools import tool, FunctionTool
from agent_framework._types import Message, Content, Role
from agent_framework._clients import BaseChatClient
from agent_framework._sessions import AgentSession, HistoryProvider, InMemoryHistoryProvider, ContextProvider
from agent_framework._mcp import MCPStreamableHTTPTool, MCPStdioTool, MCPTool
'''


def patch() -> None:
    import agent_framework

    init_path = pathlib.Path(agent_framework.__file__)
    if init_path.read_text().strip() == "":
        fd, tmp_name = tempfile.mkstemp(dir=init_path.parent, prefix=".patch_maf_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(PATCH)
            os.replace(tmp_name, init_path)
        except BaseException:
            pathlib.Path(tmp_name).unlink(missing_ok=True)
            raise
        importlib.reload(agent_framework)
        print(f"Patched {init_path}")


if __name__ == "__main__":
    patch()
