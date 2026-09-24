"""第 20 章：工作流可视化。

把真实 MAF 图输出为 Mermaid 和 Graphviz DOT，供文档展示与差异审阅。
运行 python tutorials/20-visualization/python/main.py，
在脚本同目录写入 workflow.mmd 与 workflow.dot。
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._viz import WorkflowViz  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402

OUT_DIR = pathlib.Path(__file__).resolve().parent


class UppercaseExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="uppercase")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str]) -> None:
        await ctx.send_message(message.upper())


class ValidateExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="validate")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str, str]) -> None:
        if not message.strip():
            await ctx.yield_output("[skipped]")
            return
        await ctx.send_message(message)


class LogExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="log")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[None, str]) -> None:
        await ctx.yield_output(f"LOGGED: {message}")


def build_workflow():
    up = UppercaseExecutor()
    validate = ValidateExecutor()
    log = LogExecutor()
    return (
        WorkflowBuilder(start_executor=up, name="demo-pipeline").add_edge(up, validate).add_edge(validate, log).build()
    )


def render_mermaid() -> str:
    return WorkflowViz(build_workflow()).to_mermaid()


def render_dot() -> str:
    return WorkflowViz(build_workflow()).to_digraph()


async def main() -> None:
    mermaid = render_mermaid()
    dot = render_dot()

    (OUT_DIR / "workflow.mmd").write_text(mermaid)
    (OUT_DIR / "workflow.dot").write_text(dot)

    print("=== Mermaid ===")
    print(mermaid)
    print("\n=== Graphviz DOT ===")
    print(dot)
    print(f"\nWrote workflow.mmd and workflow.dot to {OUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
