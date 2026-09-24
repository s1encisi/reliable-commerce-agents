"""
MAF v1 — 第 30 章：子工作流（Python）

两个小工作流。内层那个叫「find replacement」，它拿待替换商品去玩具级
目录里校验、查玩具级库存数，然后通过或拒绝。外层那个叫「process return」，
借助 MAF 内置的 ``WorkflowExecutor``（把 Workflow 包成 Executor）把内层
工作流当作自己图里的一个步骤来用。

不涉及 LLM —— 两个工作流都是纯粹的确定性图逻辑（与第 09 章同样以无 LLM
为先例），这样嵌套本身的机制才能成为焦点。

运行：
    python tutorials/30-subworkflows/python/main.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import (  # noqa: E402
    Executor,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowExecutor,
    handler,
)

# ─────────────── 玩具级目录数据 ───────────────

CATALOG: dict[str, str] = {
    "sku-mug-red": "Ceramic Mug — Red",
    "sku-mug-blue": "Ceramic Mug — Blue",
    "sku-plate-green": "Dinner Plate — Green",
}

STOCK: dict[str, int] = {
    "sku-mug-red": 12,
    "sku-mug-blue": 0,  # 在目录里，但没有库存
    "sku-plate-green": 5,
}


# ─────────────── 消息类型 ───────────────


@dataclass
class ReplacementRequest:
    """内层「find replacement」工作流的输入。"""

    order_id: str
    requested_product_id: str


@dataclass
class ReplacementResult:
    """内层「find replacement」工作流的输出。"""

    order_id: str
    product_id: str
    approved: bool
    reason: str


@dataclass
class ReturnRequest:
    """外层「process return」工作流的输入。"""

    order_id: str
    requested_product_id: str


# ─────────────── 内层工作流：查找替代品 ───────────────
#
# validate_catalog -> check_stock -> approve，带两条短路出口
# （目录中不存在 / 无库存），两者都直接 yield_output 一个被拒绝的
# ReplacementResult，与第 09 章的 ValidateExecutor 模式一致。


class _ValidateCatalogExecutor(Executor):
    """第 1 步：请求的商品在目录里到底存不存在？"""

    def __init__(self) -> None:
        super().__init__(id="validate_catalog")

    @handler
    async def run(
        self,
        request: ReplacementRequest,
        ctx: WorkflowContext[ReplacementRequest, ReplacementResult],
    ) -> None:
        if request.requested_product_id not in CATALOG:
            await ctx.yield_output(
                ReplacementResult(
                    order_id=request.order_id,
                    product_id=request.requested_product_id,
                    approved=False,
                    reason="not found in catalog",
                )
            )
            return
        await ctx.send_message(request)


class _CheckStockExecutor(Executor):
    """第 2 步：（现已确认存在的）商品还有库存吗？"""

    def __init__(self) -> None:
        super().__init__(id="check_stock")

    @handler
    async def run(
        self,
        request: ReplacementRequest,
        ctx: WorkflowContext[ReplacementRequest, ReplacementResult],
    ) -> None:
        if STOCK.get(request.requested_product_id, 0) <= 0:
            await ctx.yield_output(
                ReplacementResult(
                    order_id=request.order_id,
                    product_id=request.requested_product_id,
                    approved=False,
                    reason="out of stock",
                )
            )
            return
        await ctx.send_message(request)


class _ApproveExecutor(Executor):
    """第 3 步：两项检查都通过 —— 批准这次替换。"""

    def __init__(self) -> None:
        super().__init__(id="approve")

    @handler
    async def run(
        self,
        request: ReplacementRequest,
        ctx: WorkflowContext[None, ReplacementResult],
    ) -> None:
        name = CATALOG[request.requested_product_id]
        await ctx.yield_output(
            ReplacementResult(
                order_id=request.order_id,
                product_id=request.requested_product_id,
                approved=True,
                reason=f"in stock: {name}",
            )
        )


def build_find_replacement_workflow() -> Workflow:
    """构建一个全新的内层「find replacement」工作流实例。

    全新实例很重要：``WorkflowExecutor`` 的文档警告不要把一个
    ``Workflow``（及其执行器实例）共享给多个包装器，所以这里是工厂
    函数，而不是模块级单例。
    """
    validate = _ValidateCatalogExecutor()
    stock = _CheckStockExecutor()
    approve = _ApproveExecutor()
    return (
        WorkflowBuilder(start_executor=validate, name="find-replacement")
        .add_edge(validate, stock)
        .add_edge(stock, approve)
        .build()
    )


# ─────────────── 外层工作流：处理退货 ───────────────
#
# receive_return -> find_replacement（内层工作流，经 WorkflowExecutor
# 包装成单个 Executor 节点）-> finalize_return。


class _ReceiveReturnExecutor(Executor):
    """把外层请求转换成内层工作流的输入类型。"""

    def __init__(self) -> None:
        super().__init__(id="receive_return")

    @handler
    async def run(self, request: ReturnRequest, ctx: WorkflowContext[ReplacementRequest]) -> None:
        await ctx.send_message(
            ReplacementRequest(order_id=request.order_id, requested_product_id=request.requested_product_id)
        )


class _FinalizeReturnExecutor(Executor):
    """把子工作流的 ReplacementResult 转成外层工作流的最终文本输出。"""

    def __init__(self) -> None:
        super().__init__(id="finalize_return")

    @handler
    async def run(self, result: ReplacementResult, ctx: WorkflowContext[None, str]) -> None:
        if result.approved:
            await ctx.yield_output(
                f"Return {result.order_id}: replacement {result.product_id} approved and shipped ({result.reason})."
            )
        else:
            await ctx.yield_output(
                f"Return {result.order_id}: replacement {result.product_id} rejected ({result.reason}) "
                "— issuing a refund instead."
            )


def build_process_return_workflow() -> Workflow:
    """构建外层「process return」工作流，并在其中嵌套一个全新的内层工作流。"""
    receive = _ReceiveReturnExecutor()
    find_replacement = WorkflowExecutor(
        build_find_replacement_workflow(),
        id="find_replacement",
        # 默认值（False）：子工作流 yield_output(ReplacementResult) 的结果
        # 会作为一次普通的 send_message() 转发给本节点出边所指向的目标 ——
        # 这里是 finalize_return。若置为 True，则子工作流的输出会直接成为
        # 外层工作流自己的终止输出，从而完全跳过 finalize_return。
        allow_direct_output=False,
    )
    finalize = _FinalizeReturnExecutor()
    return (
        WorkflowBuilder(start_executor=receive, name="process-return")
        .add_edge(receive, find_replacement)
        .add_edge(find_replacement, finalize)
        .build()
    )


# ─────────────── 运行辅助函数 ───────────────


async def run_find_replacement(order_id: str, requested_product_id: str) -> ReplacementResult | None:
    """单独运行内层工作流，返回它那唯一一条 ReplacementResult 输出。"""
    workflow = build_find_replacement_workflow()
    request = ReplacementRequest(order_id=order_id, requested_product_id=requested_product_id)
    async for event in workflow.run(request, stream=True):
        if getattr(event, "type", None) == "output":
            return getattr(event, "data", None)
    return None


async def run_process_return(order_id: str, requested_product_id: str) -> list[str]:
    """运行外层工作流（其中嵌套了内层工作流），返回所有被 yield 的输出。"""
    workflow = build_process_return_workflow()
    request = ReturnRequest(order_id=order_id, requested_product_id=requested_product_id)
    outputs: list[str] = []
    async for event in workflow.run(request, stream=True):
        if getattr(event, "type", None) == "output":
            outputs.append(getattr(event, "data", None))
    return outputs


async def main() -> None:
    scenarios = [
        ("R-1001", "sku-mug-red"),  # 在目录中且有库存 -> 批准
        ("R-1002", "sku-mug-blue"),  # 在目录中但无库存 -> 拒绝
        ("R-1003", "sku-unknown"),  # 根本不在目录中 -> 拒绝
    ]
    for order_id, product_id in scenarios:
        print(f"--- Return {order_id}: requested replacement {product_id!r} ---")
        for output in await run_process_return(order_id, product_id):
            print(output)
        print()


if __name__ == "__main__":
    asyncio.run(main())
