"""仓库根目录解析的 Docker 回归测试。

本地四级路径与镜像 /app/shared/config.py 的父目录数量不同；
不能无条件读取 parents[3]，否则所有智能体在导入时就会崩溃。
"""

from __future__ import annotations

from pathlib import Path

from shared.config import _resolve_repo_root


def test_resolves_three_levels_up_on_the_host_layout() -> None:
    # 从本地 agents/python/shared/config.py 定位仓库根。
    host_path = Path("/home/user/reliable-commerce-agents/agents/python/shared/config.py")
    assert _resolve_repo_root(host_path) == Path("/home/user/reliable-commerce-agents")


def test_falls_back_to_the_immediate_parent_on_the_shallow_docker_layout() -> None:
    # 镜像路径只有 /app 与 / 两级父目录，
    # 越界时必须回退，不能抛出 IndexError。
    docker_path = Path("/app/shared/config.py")
    assert _resolve_repo_root(docker_path) == Path("/app/shared")
