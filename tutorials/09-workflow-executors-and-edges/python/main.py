"""
MAF v1 — 第 09 章：工作流执行器与边（Python）

三个执行器用边串起来，再加一条条件边，依据上一个执行器的输出做路由。
不涉及 LLM —— 工作流是确定性的协调者；智能体要到第 11 章才回归。

运行：
    python tutorials/09-workflow-executors-and-edges/python/main.py "ord-8842"
    python tutorials/09-workflow-executors-and-edges/python/main.py ""   # 空串 → 短路
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402

# ─────────────── 执行器 ───────────────

class NormalizeOrderExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="normalize-order")

    @handler
    async def run(self, order_id: str, ctx: WorkflowContext[str]) -> None:
        await ctx.send_message(order_id.strip().upper())


class ValidateOrderExecutor(Executor):
    """把合法的订单号送往下游；把空订单号短路成一条终止输出。"""

    def __init__(self) -> None:
        super().__init__(id="validate-order")

    @handler
    async def run(self, order_id: str, ctx: WorkflowContext[str, str]) -> None:
        if not order_id:
            # 产出一条终止工作流的输出；下游执行器不会再运行。
            await ctx.yield_output("[rejected: empty order id]")
            return
        await ctx.send_message(order_id)


class LogOrderExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="log-order")

    @handler
    async def run(self, order_id: str, ctx: WorkflowContext[None, str]) -> None:
        await ctx.yield_output(f"ORDER LOGGED: {order_id}")


# ─────────────── 构建 + 运行 ───────────────

def build_workflow():
    normalize = NormalizeOrderExecutor()
    validate = ValidateOrderExecutor()
    log = LogOrderExecutor()
    return (
        WorkflowBuilder(start_executor=normalize)
        .add_edge(normalize, validate)
        .add_edge(validate, log)
        .build()
    )


async def run(order_id: str) -> list[object]:
    """运行该工作流，返回所有被 yield 的工作流输出。"""
    workflow = build_workflow()
    outputs: list[object] = []
    async for event in workflow.run(order_id, stream=True):
        # WorkflowEvent 是一个带标签的联合类型；按其 `type` 字段过滤。
        if getattr(event, "type", None) == "output":
            outputs.append(getattr(event, "data", None))
    return outputs


async def main() -> None:
    order_id = sys.argv[1] if len(sys.argv) > 1 else "ord-8842"
    print(f"输入：{order_id!r}")
    for output in await run(order_id):
        print(f"输出：{output!r}")


if __name__ == "__main__":
    asyncio.run(main())
