"""
MAF v1 —— 第 18 章：状态与检查点（Python）

双执行器工作流：ReturnRequestExecutor 在处理退货明细项时累加退款金额，
然后转发给 FinalizeReturnExecutor，后者把退款总额作为工作流输出产出。
MAF 在每个超步边界做检查点；我们通过 FileCheckpointStorage 持久化快照。

端到端跑完后，我们丢弃第一个工作流实例，构建一个带全新
ReturnRequestExecutor（不同的初始退款！）的新实例，并从第一个检查点
恢复——以此证明执行器状态（累加中的 refund_amount）能通过磁盘上的
JSON 完成往返。

这是对生产用 ``workflow:return-replace`` 链条
（`agents/python/workflows/return_replace.py`）的一个小型近似——那条
工作流要带着大得多的 ``WorkflowState`` 穿过六个受人在回路管控的步骤。
本章只在示例规模上讲授检查点保存/恢复这一机制本身。

运行：
    python tutorials/18-state-and-checkpoints/python/main.py                 # initial=10.0 item=5.0 -> 15.0
    python tutorials/18-state-and-checkpoints/python/main.py 10.0 5.0
"""

import asyncio
import pathlib
import shutil
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework._workflows._checkpoint import FileCheckpointStorage  # noqa: E402
from agent_framework._workflows._executor import Executor, handler  # noqa: E402
from agent_framework._workflows._workflow_builder import WorkflowBuilder  # noqa: E402
from agent_framework._workflows._workflow_context import WorkflowContext  # noqa: E402

CHECKPOINT_DIR = pathlib.Path(__file__).resolve().parent / ".checkpoints"
WORKFLOW_NAME = "return-request-workflow"


class ReturnRequestExecutor(Executor):
    """退货请求的累加退款总额。把更新后的 refund_amount 转发给下一个执行器。

    状态（``self.refund_amount``）由 ``on_checkpoint_save`` 捕获进检查点，
    并由 ``on_checkpoint_restore`` 重新水合。
    """

    def __init__(self, initial_refund: float) -> None:
        super().__init__(id="return-request")
        self.refund_amount = initial_refund

    @handler
    async def handle(self, item_refund: float, ctx: WorkflowContext[float, None]) -> None:
        self.refund_amount += item_refund
        await ctx.send_message(self.refund_amount)

    async def on_checkpoint_save(self) -> dict[str, Any]:
        return {"refund_amount": self.refund_amount}

    async def on_checkpoint_restore(self, state: dict[str, Any]) -> None:
        self.refund_amount = float(state.get("refund_amount", 0.0))


class FinalizeReturnExecutor(Executor):
    """无状态的终端节点：把收到的退款总额作为输出产出。"""

    def __init__(self) -> None:
        super().__init__(id="finalize-return")

    @handler
    async def handle(self, refund_amount: float, ctx: WorkflowContext[None, float]) -> None:
        await ctx.yield_output(refund_amount)


def build_workflow(storage: FileCheckpointStorage, *, initial_refund: float):
    return_request = ReturnRequestExecutor(initial_refund)
    finalize = FinalizeReturnExecutor()
    return (
        WorkflowBuilder(
            start_executor=return_request,
            name=WORKFLOW_NAME,
            checkpoint_storage=storage,
        )
        .add_edge(return_request, finalize)
        .build()
    )


async def run_once(storage: FileCheckpointStorage, *, initial_refund: float, item_refund: float) -> float:
    """端到端运行工作流并返回最终退款金额。"""
    workflow = build_workflow(storage, initial_refund=initial_refund)
    outputs: list[float] = []
    async for event in workflow.run(item_refund, stream=True):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, (int, float)):
                outputs.append(data)
    return outputs[-1] if outputs else 0.0


async def resume_from_checkpoint(
    storage: FileCheckpointStorage,
    checkpoint_id: str,
    *,
    resume_initial_refund: float,
) -> float:
    """构建一个全新工作流（带不同的初始退款！）并从检查点恢复。

    如果检查点生效，恢复后的 ReturnRequestExecutor 的 ``refund_amount``
    来自检查点，而不是 ``resume_initial_refund``——以此证明状态能挺过
    ``ReturnRequestExecutor(initial_refund=resume_initial_refund)`` 的
    全新构造。
    """
    workflow = build_workflow(storage, initial_refund=resume_initial_refund)
    outputs: list[float] = []
    async for event in workflow.run(
        stream=True,
        checkpoint_id=checkpoint_id,
        checkpoint_storage=storage,
    ):
        if getattr(event, "type", None) == "output":
            data = getattr(event, "data", None)
            if isinstance(data, (int, float)):
                outputs.append(data)
    return outputs[-1] if outputs else 0.0


async def demo(initial_refund: float, item_refund: float) -> None:
    if CHECKPOINT_DIR.exists():
        shutil.rmtree(CHECKPOINT_DIR)
    CHECKPOINT_DIR.mkdir()
    storage = FileCheckpointStorage(str(CHECKPOINT_DIR))

    # ─── 阶段 1：端到端运行，每个超步都会写入检查点 ──
    print(f"阶段 1：initial_refund={initial_refund}，item_refund={item_refund}")
    result = await run_once(storage, initial_refund=initial_refund, item_refund=item_refund)
    print(f"阶段 1 结果：refund_amount = {result}")

    files = list(CHECKPOINT_DIR.iterdir())
    print(f"\n磁盘上有 {len(files)} 个检查点文件。")

    # ─── 阶段 2：用错误的初始退款重新水合到一个全新工作流 ─
    # 用 999.0 播种即可证明检查点才是真正的信息来源：恢复后的
    # ReturnRequestExecutor 以 self.refund_amount = 999.0 起步，
    # 随后 on_checkpoint_restore 用快照中的 refund_amount 覆盖它，
    # 这一覆盖发生在 FinalizeReturn 的超步运行之前。
    #
    # 我们取*第一个*检查点（超步 1，即 FinalizeReturn 产出输出之前）。
    # 从最新的检查点恢复会重放一个没有待处理消息的工作流——MAF 会
    # 愉快地完成，却不产出任何输出。
    checkpoints = await storage.list_checkpoints(workflow_name=WORKFLOW_NAME)
    if not checkpoints:
        print("未产生任何检查点——无可恢复。")
        return
    checkpoints.sort(key=lambda cp: cp.timestamp)
    first = checkpoints[0]

    wrong_initial_refund = 999.0
    print(f"从 {first.checkpoint_id[:8]}… 恢复，initial_refund={wrong_initial_refund}")
    replayed = await resume_from_checkpoint(
        storage, first.checkpoint_id, resume_initial_refund=wrong_initial_refund
    )
    print(f"阶段 2 结果：refund_amount = {replayed}（期望 {result}）")


async def main() -> None:
    initial_refund = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    item_refund = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    await demo(initial_refund, item_refund)


if __name__ == "__main__":
    asyncio.run(main())
