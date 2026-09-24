"""
MAF v1 — 第 10 章：工作流事件与构建器（Python）

在第 09 章的流水线上扩展出一种*自定义*进度事件。每个执行器都通过
ctx.yield_output() 产出 ProgressPayload('executor-id', percent)，好让调用方
在工作流运行时展示实时进度指示 —— 它与流水线的最终结果是两回事。

「进度」与「最终输出」是构建期的定性，而不是每次调用的选择：某个执行器发出的
每一次 yield_output() 都带同一个事件类型，由该执行器列在 WorkflowBuilder 的
intermediate_output_from（进度形状）还是 output_from（最终结果形状）之下决定。
这就是为什么 `normalize` 与 `validate` 永远只 yield ProgressPayload，而 `log`
是唯一 yield 流水线真实结果的执行器 —— 早先的 `WorkflowEvent.emit()` API 允许
一个执行器自由混用两者，但该 API 已被弃用，改用现在这种显式划分
（见 `agent_framework._workflows._events.WorkflowEvent.emit` 的模块文档字符串）。

运行：
    python tutorials/10-workflow-events-and-builder/python/main.py "ord-8842"
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402

# ─────────────── 自定义事件载荷 ───────────────

@dataclass(frozen=True)
class ProgressPayload:
    step: str
    percent: int


# ─────────────── 执行器 ───────────────

class NormalizeOrderExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="normalize-order")

    @handler
    async def run(self, order_id: str, ctx: WorkflowContext[str, ProgressPayload]) -> None:
        await ctx.yield_output(ProgressPayload("normalize-order", 33))
        await ctx.send_message(order_id.strip().upper())


class ValidateOrderExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="validate-order")

    @handler
    async def run(self, order_id: str, ctx: WorkflowContext[str, ProgressPayload | str]) -> None:
        await ctx.yield_output(ProgressPayload("validate-order", 66))
        if not order_id:
            # 在 log 运行之前就短路。由于 validate 被指定为「中间输出」
            # （见下方的 intermediate_output_from），这次 yield 与上面那条
            # 进度载荷携带同一事件类型 —— 在这里没问题，因为调用方是靠
            # 载荷形状（ProgressPayload 还是普通 str）来区分进度与结果的，
            # 而不是靠工作流自己的 output/intermediate 标签。见 main_test.py。
            await ctx.yield_output("[rejected: empty order id]")
            return
        await ctx.send_message(order_id)


class LogOrderExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="log-order")

    @handler
    async def run(self, order_id: str, ctx: WorkflowContext[None, ProgressPayload | str]) -> None:
        await ctx.yield_output(ProgressPayload("log-order", 100))
        await ctx.yield_output(f"ORDER LOGGED: {order_id}")


# ─────────────── 构建 + 运行 ───────────────

def build_workflow():
    normalize = NormalizeOrderExecutor()
    validate = ValidateOrderExecutor()
    log = LogOrderExecutor()
    return (
        WorkflowBuilder(
            start_executor=normalize,
            intermediate_output_from=[normalize, validate],
            output_from=[log],
        )
        .add_edge(normalize, validate)
        .add_edge(validate, log)
        .build()
    )


async def run_with_events(order_id: str) -> tuple[list[ProgressPayload], list[object]]:
    """运行工作流并返回（进度事件, 最终输出）。

    按载荷形状（isinstance ProgressPayload）分桶，而不是按工作流自身的
    type='output' / type='intermediate' 标签 —— validate 提前退出时 yield 的
    "[rejected: empty order id]" 与它所属执行器的「中间输出」定性相同
    （见 ValidateOrderExecutor），所以单看类型标签无法在这里区分进度与结果。
    而载荷形状可以。
    """
    workflow = build_workflow()
    progress: list[ProgressPayload] = []
    outputs: list[object] = []
    async for event in workflow.run(order_id, stream=True):
        etype = getattr(event, "type", None)
        if etype not in ("output", "intermediate"):
            continue
        data = getattr(event, "data", None)
        if isinstance(data, ProgressPayload):
            progress.append(data)
        else:
            outputs.append(data)
    return progress, outputs


async def main() -> None:
    order_id = sys.argv[1] if len(sys.argv) > 1 else "ord-8842"
    print(f"input: {order_id!r}")
    progress, outputs = await run_with_events(order_id)
    for p in progress:
        print(f"  progress: {p.step} → {p.percent}%")
    for output in outputs:
        print(f"output: {output!r}")


if __name__ == "__main__":
    asyncio.run(main())
