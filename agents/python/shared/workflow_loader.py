"""根据 YAML 声明构建 MAF 工作流。

读取 agents/python/config/workflows/*.yaml，按 op 注册表创建执行器
和连边。内置 passthrough、upper、lower、strip、reverse、non_empty
与 prefix；分别支持透传、字符串变换、空值短路和带前缀的终止输出。

_OPS 是扩展入口，新增操作只需函数与注册项。当前是教学性质的声明式
加载器，不能把未来的生产工作流注册计划写成已经实现的能力。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._workflow import Workflow
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext

# ─────────────────────── Op registry ───────────────────────


OpFn = Callable[[str], tuple[str | None, str | None]]
"""An op takes an input string and returns (forwarded, terminal).

Exactly one of the two values should be non-None:

- ``forwarded`` is sent downstream via ``ctx.send_message``.
- ``terminal`` is yielded as a workflow output and halts that branch.
"""


def _op_passthrough(_: dict[str, Any]) -> OpFn:
    return lambda s: (s, None)


def _op_upper(_: dict[str, Any]) -> OpFn:
    return lambda s: (s.upper(), None)


def _op_lower(_: dict[str, Any]) -> OpFn:
    return lambda s: (s.lower(), None)


def _op_strip(_: dict[str, Any]) -> OpFn:
    return lambda s: (s.strip(), None)


def _op_reverse(_: dict[str, Any]) -> OpFn:
    return lambda s: (s[::-1], None)


def _op_non_empty(config: dict[str, Any]) -> OpFn:
    empty_msg = config.get("empty_output", "[skipped: empty input]")

    def _impl(s: str) -> tuple[str | None, str | None]:
        return (s, None) if s.strip() else (None, empty_msg)

    return _impl


def _op_prefix(config: dict[str, Any]) -> OpFn:
    prefix = config.get("prefix", "")

    def _impl(s: str) -> tuple[str | None, str | None]:
        return (None, f"{prefix}{s}")

    return _impl


_OPS: dict[str, Callable[[dict[str, Any]], OpFn]] = {
    "passthrough": _op_passthrough,
    "upper": _op_upper,
    "lower": _op_lower,
    "strip": _op_strip,
    "reverse": _op_reverse,
    "non_empty": _op_non_empty,
    "prefix": _op_prefix,
}


def register_op(name: str, factory: Callable[[dict[str, Any]], OpFn]) -> None:
    """注册新的操作名称，可用于包装领域工具行为。"""
    _OPS[name] = factory


# ─────────────────────── Executor ───────────────────────


class DeclarativeExecutor(Executor):
    """由注册操作和配置驱动的执行器。"""

    def __init__(self, executor_id: str, op: str, config: dict[str, Any]) -> None:
        super().__init__(id=executor_id)
        if op not in _OPS:
            raise ValueError(f"Unknown op {op!r} for executor {executor_id!r}. Registered: {sorted(_OPS)}")
        self._op = _OPS[op](config)

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str, str]) -> None:
        forward, terminal = self._op(message)
        if terminal is not None:
            await ctx.yield_output(terminal)
            return
        if forward is not None:
            await ctx.send_message(forward)


# ─────────────────────── Loader ───────────────────────


class WorkflowSpecError(ValueError):
    """YAML 工作流结构不合法时抛出的异常。"""


def load_workflow(spec_path: str | Path) -> Workflow:
    """从 YAML 文件加载工作流。

    文件缺失、格式错误或引用未声明执行器时，抛出含明确说明的
    WorkflowSpecError。
    """
    path = Path(spec_path)
    if not path.is_file():
        raise WorkflowSpecError(f"Workflow spec not found: {path}")

    try:
        spec = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise WorkflowSpecError(f"Malformed YAML in {path}: {exc}") from exc

    if not isinstance(spec, dict):
        raise WorkflowSpecError(f"{path}: top-level must be a mapping, got {type(spec).__name__}")

    for required in ("name", "start", "executors", "edges"):
        if required not in spec:
            raise WorkflowSpecError(f"{path}: missing required key {required!r}")

    # 创建执行器。
    executors_by_id: dict[str, DeclarativeExecutor] = {}
    for raw in spec["executors"]:
        if not isinstance(raw, dict):
            raise WorkflowSpecError(f"{path}: each executor entry must be a mapping, got {raw!r}")
        eid = raw.get("id")
        op = raw.get("op")
        if not eid or not op:
            raise WorkflowSpecError(f"{path}: executor entries need both 'id' and 'op', got {raw!r}")
        if eid in executors_by_id:
            raise WorkflowSpecError(f"{path}: duplicate executor id {eid!r}")
        config = {k: v for k, v in raw.items() if k not in {"id", "op"}}
        executors_by_id[eid] = DeclarativeExecutor(eid, op, config)

    # 验证起点和边只引用已声明标识。
    start_id = spec["start"]
    if start_id not in executors_by_id:
        raise WorkflowSpecError(
            f"{path}: start={start_id!r} is not among declared executor ids ({sorted(executors_by_id)})"
        )

    builder = WorkflowBuilder(
        start_executor=executors_by_id[start_id],
        name=spec["name"],
        description=spec.get("description"),
    )

    edges = spec["edges"]
    if not isinstance(edges, list):
        raise WorkflowSpecError(f"{path}: edges must be a list, got {type(edges).__name__}")

    for edge in edges:
        if not isinstance(edge, dict) or "from" not in edge or "to" not in edge:
            raise WorkflowSpecError(f"{path}: each edge needs 'from' and 'to', got {edge!r}")
        source_id = edge["from"]
        target_id = edge["to"]
        if source_id not in executors_by_id:
            raise WorkflowSpecError(f"{path}: edge source {source_id!r} is not declared")
        if target_id not in executors_by_id:
            raise WorkflowSpecError(f"{path}: edge target {target_id!r} is not declared")
        builder = builder.add_edge(executors_by_id[source_id], executors_by_id[target_id])

    return builder.build()


def load_workflows_directory(directory: str | Path) -> dict[str, Workflow]:
    """加载目录中全部 YAML，返回名称到工作流的映射。

    供工作流可视化脚本等批量使用。
    """
    path = Path(directory)
    if not path.is_dir():
        raise WorkflowSpecError(f"Workflow directory not found: {path}")

    workflows: dict[str, Workflow] = {}
    for spec_path in sorted(path.glob("*.yaml")):
        workflow = load_workflow(spec_path)
        workflows[spec_path.stem] = workflow
    return workflows
