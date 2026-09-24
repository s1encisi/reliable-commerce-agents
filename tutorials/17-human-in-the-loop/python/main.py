"""
MAF v1 —— 第 17 章：人在回路（Python）

一个在执行途中暂停、向人询问输入、再带着回答恢复的工作流。领域：退款
审批——执行器持有待处理的退款，通过 request_info 暂停，请人工审批人
批准或驳回，退款才真正生效。

交互式运行：
    python tutorials/17-human-in-the-loop/python/main.py
"""

import asyncio
import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._request_info_mixin import response_handler  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402

# ─────────────── 请求 / 响应结构 ───────────────

@dataclass(frozen=True)
class RefundApprovalRequest:
    """一笔等待人工决策的退款。

    它同时充当两重角色：工作流的启动输入（客户希望退款的 order_id 与
    amount），以及工作流暂停时交给调用方的载荷——中途无需再派生任何
    额外信息。
    """
    order_id: str
    amount: float


# ─────────────── 执行器 ───────────────

class RefundApprovalGate(Executor):
    """
    接收一笔待处理的退款。每次运行时它通过 request_info 暂停，请人工
    审批人批准或驳回。决策到达后，它产出结果。
    """

    def __init__(self) -> None:
        super().__init__(id="refund-approval-gate")

    @handler
    async def start(self, refund: RefundApprovalRequest, ctx: WorkflowContext[None, str]) -> None:
        # 暂停工作流，等待人工的批准/驳回决策。
        # `bool` 类型告诉 MAF 响应应为何种形状。
        await ctx.request_info(request_data=refund, response_type=bool)

    @response_handler
    async def check(
        self,
        request: RefundApprovalRequest,
        approved: bool,
        ctx: WorkflowContext[None, str],
    ) -> None:
        if approved:
            await ctx.yield_output(f"订单 {request.order_id} 退款已批准：¥{request.amount:.2f}")
        else:
            await ctx.yield_output(f"订单 {request.order_id} 退款已驳回")


def build_workflow():
    gate = RefundApprovalGate()
    return WorkflowBuilder(start_executor=gate).build()


# ─────────────── 驱动 ───────────────

async def run_with_response(order_id: str, amount: float, approved: bool) -> str:
    """运行一次，并在工作流暂停时喂入一个预设的审批决策。"""
    workflow = build_workflow()

    # 首次运行——工作流在 request_info 处暂停。把整个流消费完，使工作流的
    # 内部运行状态在恢复之前干净地回到空闲。
    pending_request_id: str | None = None
    async for event in workflow.run(RefundApprovalRequest(order_id=order_id, amount=amount), stream=True):
        if pending_request_id is None and getattr(event, "type", None) == "request_info":
            pending_request_id = getattr(event, "request_id", None)

    assert pending_request_id, "预期应出现 request_info 事件使工作流暂停"

    # 带着预设决策恢复。运行会继续产出事件直至完成。
    outputs: list[str] = []
    async for event in workflow.run(
        responses={pending_request_id: approved},
        stream=True,
    ):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, str):
                outputs.append(data)
    return outputs[-1] if outputs else ""


async def main() -> None:
    order_id = "ord-482"
    amount = 245.50
    workflow = build_workflow()

    pending_request_id: str | None = None
    request_data: RefundApprovalRequest | None = None
    async for event in workflow.run(RefundApprovalRequest(order_id=order_id, amount=amount), stream=True):
        if getattr(event, "type", None) == "request_info":
            pending_request_id = getattr(event, "request_id", None)
            request_data = getattr(event, "data", None)
            break

    if not pending_request_id or request_data is None:
        print("工作流未暂停即结束——不符合预期。")
        return

    prompt = f"是否批准订单 {request_data.order_id} 的退款 ¥{request_data.amount:.2f}？[y/n]："
    answer = input(prompt).strip().lower()
    approved = answer in ("y", "yes")

    async for event in workflow.run(
        responses={pending_request_id: approved},
        stream=True,
    ):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, str):
                print(data)


if __name__ == "__main__":
    asyncio.run(main())
