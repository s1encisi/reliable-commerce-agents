"""
所有教程章节测试的根 conftest。

注册各章共用的自定义标记，避免 pytest 对它们发出警告。
"""

import sys

# 每个章节的 tests/test_*.py 都会把自己的 `<chapter>/python` 目录插到
# sys.path 最前面，然后执行 `from main import ...`（部分章节还会导入同名的
# 辅助模块，例如第 08 章的 `weather_mcp_server`）。
# 当每个章节在各自进程中运行时这是正确的；但在同一个 pytest 会话中收集
# 整个 `tutorials/` 目录树时，被收集的*第二个*章节会复用*第一个*章节缓存在
# `sys.modules` 中的 `main` 模块，因为缓存键是模块名而非路径。
# 因此在每个测试模块被收集之前，先清掉这些临时的、章节局部的模块名，
# 让每个章节都从自己的 sys.path 条目重新导入自己的 `main.py`。
_TRANSIENT_MODULES = ("main", "weather_mcp_server")


def pytest_configure(config):  # noqa: ANN001 - pytest 钩子签名
    config.addinivalue_line(
        "markers",
        "integration: 该测试会访问真实 LLM；缺少凭据时跳过",
    )


def pytest_collectstart(collector):  # noqa: ANN001 - pytest 钩子签名
    for name in _TRANSIENT_MODULES:
        sys.modules.pop(name, None)
