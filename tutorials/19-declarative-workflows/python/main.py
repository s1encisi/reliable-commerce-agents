"""
MAF v1 —— 第 19 章：声明式工作流（Python）

在运行时从 YAML 文件加载工作流，而不是用 Python 手写图结构。用一个
最小化的、专门为此构建的 schema 来演示声明式编排的原理。

运行：
    python tutorials/19-declarative-workflows/python/main.py "hello"
    python tutorials/19-declarative-workflows/python/main.py ""     # 短路
"""

import asyncio
import pathlib
import sys
from collections.abc import Callable
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

import yaml  # noqa: E402
from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._workflow import Workflow  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402

SPEC_PATH = pathlib.Path(__file__).resolve().parent / "workflow.yaml"


# ─────────────── YAML 可引用的内置「算子」 ───────────────

def _build_op(op: str, config: dict[str, Any]) -> Callable[[str], tuple[str | None, str | None]]:
    """返回一个纯函数：input_text -> (forwarded_text, terminal_text)。

    若 forwarded_text 不为 None → 用 ctx.send_message 转发它。
    若 terminal_text 不为 None → 用 ctx.yield_output 产出它。
    """
    if op == "upper":
        return lambda s: (s.upper(), None)
    if op == "lower":
        return lambda s: (s.lower(), None)
    if op == "strip":
        return lambda s: (s.strip(), None)
    if op == "reverse":
        return lambda s: (s[::-1], None)
    if op == "non_empty":
        def _non_empty(s: str) -> tuple[str | None, str | None]:
            return (s, None) if s.strip() else (None, "[已跳过：输入为空]")
        return _non_empty
    if op == "prefix":
        prefix = config.get("prefix", "")
        return lambda s: (None, f"{prefix}{s}")
    raise ValueError(f"未知算子：{op!r}")


class DeclarativeExecutor(Executor):
    """行为由 YAML 中的 'op' 字符串定义的执行器。"""

    def __init__(self, executor_id: str, op: str, config: dict[str, Any]) -> None:
        super().__init__(id=executor_id)
        self._op = _build_op(op, config)

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str, str]) -> None:
        forward, terminal = self._op(message)
        if terminal is not None:
            await ctx.yield_output(terminal)
            return
        if forward is not None:
            await ctx.send_message(forward)


# ─────────────── 加载器 ───────────────

def load_workflow(spec_path: pathlib.Path = SPEC_PATH) -> Workflow:
    spec = yaml.safe_load(spec_path.read_text())
    executors_by_id: dict[str, DeclarativeExecutor] = {}
    for entry in spec["executors"]:
        executor_id = entry["id"]
        op = entry["op"]
        config = {k: v for k, v in entry.items() if k not in {"id", "op"}}
        executors_by_id[executor_id] = DeclarativeExecutor(executor_id, op, config)

    start = executors_by_id[spec["start"]]
    builder = WorkflowBuilder(start_executor=start, name=spec.get("name", "declarative"))
    for edge in spec["edges"]:
        builder = builder.add_edge(executors_by_id[edge["from"]], executors_by_id[edge["to"]])
    return builder.build()


async def run(text: str) -> list[object]:
    workflow = load_workflow()
    outputs: list[object] = []
    async for event in workflow.run(text, stream=True):
        if getattr(event, "type", None) == "output":
            outputs.append(getattr(event, "data", None))
    return outputs


async def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else "hello world"
    print(f"规格：{SPEC_PATH.name}")
    print(f"输入：{text!r}")
    for output in await run(text):
        print(f"输出：{output!r}")


if __name__ == "__main__":
    asyncio.run(main())
