"""第 20b 章 —— DevUI：冒烟测试。

这些**不是**针对运行中 DevUI 服务的集成测试——在 pytest 内启动 FastAPI
进程不稳定且超出范围。我们改为断言：

1. 模块能干净导入（捕捉 DevUI 包中的拼写错误 / 导入漂移）。
2. `build_agent()` 返回看起来像 MAF Agent 的对象。
3. 演示智能体以预期名称注册，使 `serve(entities=[...])` 调用时 DevUI 的
   实体注册表能取到正确的 id。
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _llm_credentials_present() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return all(os.environ.get(k) for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_KEY", "AZURE_OPENAI_DEPLOYMENT"))
    return bool(os.environ.get("OPENAI_API_KEY"))


pytestmark = pytest.mark.skipif(
    not _llm_credentials_present(),
    reason="缺少 LLM 凭据——build_agent() 需要一个聊天客户端密钥才能构造。",
)


def _import_own_main():
    """重新导入本章自己的 main.py，而不是别的章节的。

    在同一个 pytest 会话中收集整个 tutorials/ 目录，意味着许多章节在
    sys.modules 里共用模块名 "main"。与其它每一章（在模块导入时执行一次
    `from main import ...`，正好赶上 tutorials/conftest.py 的
    pytest_collectstart 逐出）不同，这些测试在每个测试*函数*内部惰性执行
    `import main`——那发生在 pytest 的执行阶段，此时每一章的模块都已被收集
    完毕，collectstart 的逐出早已过时。到那时 sys.modules["main"] 持有的是
    最后被收集的那一章，而模块作用域的朴素 `sys.path.insert(0, ...)`
    （上面第 25 行）也无济于事，因为后收集的章节自己的 insert 会把本章的
    条目挤到后面。因此在每次导入之前，把我们自己的目录重新插到最前面，
    并立即逐出过期的缓存。
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    sys.modules.pop("main", None)
    import main

    return main


def test_main_module_imports() -> None:
    """DevUI + MAF 的导入都能正常解析。"""
    from agent_framework.devui import serve

    _import_own_main()

    assert callable(serve)


def test_build_agent_returns_agent_instance() -> None:
    """build_agent() 返回 MAF Agent 对象。"""
    from agent_framework import Agent

    main = _import_own_main()

    agent = main.build_agent()
    assert isinstance(agent, Agent)


def test_build_agent_has_expected_name() -> None:
    """DevUI 以 name 作为实体键——锁定该 id，使 URL / 元数据保持稳定。"""
    main = _import_own_main()

    agent = main.build_agent()
    assert agent.name == "devui-demo"
