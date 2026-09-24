"""退货与换货工作流 —— 带人工参与（HITL）闸门的 MAF 顺序编排。

步骤链：check-eligibility → hitl-gate → initiate-return →
search-replacements → apply-discount → finalize。

资格校验服务提供通用的审批策略：当启用 HITL，或者可信订单总额超过
``settings.RETURN_HITL_THRESHOLD`` 时，就需要审批。闸门会在创建任何退货之前，
通过 ``ctx.request_info`` 发出一个已绑定参数的快照。恢复执行时会还原归属人
和精确参数；共享服务会再次检查当前资格。

已按 ``plans/refactor/09-return-replace-sequential-hitl.md`` 从自定义的顺序状态机
重构为 MAF ``WorkflowBuilder``。公共 API —— 类、dataclass、``execute(state) -> state``
签名 —— 均保持不变，调用方无需改动。

注意：这里不要添加 ``from __future__ import annotations``。MAF 的
``@response_handler`` 会在导入时通过 ``inspect.signature`` 解析参数类型；
字符串化的注解会破坏该解析。
"""

import logging
import math
from dataclasses import dataclass, field

from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._request_info_mixin import response_handler
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext

from shared.after_sales.approval import ReturnApproval, current_return_approval, payload_hash
from shared.after_sales.operations import current_operation_id, operation_id_for
from shared.after_sales.policy import APPROVAL_TTL, POLICY_VERSION
from shared.after_sales.service import utc_now
from shared.config import settings
from shared.context import current_user_email, current_user_role
from shared.tool_inputs import InitiateReturnInput

logger = logging.getLogger(__name__)


@dataclass
class WorkflowState:
    """在各执行器之间传递工作流的进行中状态。"""

    user_email: str
    order_id: str
    order_total: float = 0.0
    reason: str = ""
    order_revision: str = ""
    requires_approval: "bool | None" = None
    approval: "dict | None" = None
    outcome: str = "READY"
    operation_id: "str | None" = None
    existing_operation: "dict | None" = None

    # 沿链路逐步填充
    return_eligible: bool = False
    return_id: "str | None" = None
    refund_amount: float = 0.0
    replacement_products: list = field(default_factory=list)
    applied_discount: "dict | None" = None

    # HITL 状态
    hitl_requested: bool = False
    hitl_approved: "bool | None" = None

    # 执行追踪
    completed_steps: list = field(default_factory=list)
    errors: list = field(default_factory=list)


@dataclass
class ReturnApprovalRequest:
    """HITL 闸门针对高价值退货发出的载荷。"""

    order_id: str
    order_total: float
    refund_amount: float
    replacement_count: int
    user_email: str = ""
    reason: str = ""
    approval: "dict | None" = None
    operation_id: "str | None" = None


# ─────────────────────── 执行器 ───────────────────────


class _CheckEligibilityExecutor(Executor):
    def __init__(self, tools: dict) -> None:
        super().__init__(id="check-eligibility")
        self._tools = tools

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        fn = self._tools.get("check_return_eligibility")
        if not fn:
            state.errors.append("check_return_eligibility tool not available")
            await ctx.yield_output(state)
            return
        try:
            result = await fn(order_id=state.order_id)
        except Exception:
            logger.exception("Return eligibility lookup failed")
            state.outcome = "NEEDS_REVIEW"
            state.errors.append("Return eligibility could not be verified. Please try again later.")
            await ctx.yield_output(state)
            return

        if not isinstance(result, dict) or not isinstance(result.get("eligible", False), bool):
            state.outcome = "FAILED_FINAL"
            state.errors.append("Eligibility tool returned an invalid result; no return was submitted.")
            await ctx.yield_output(state)
            return
        state.return_eligible = bool(result.get("eligible"))
        try:
            intent = InitiateReturnInput(
                order_id=state.order_id,
                reason=state.reason or "Customer requested replacement",
                refund_method="store_credit",
            )
            state.operation_id = str(operation_id_for(intent))
        except ValueError:
            state.operation_id = None
            if result.get("policy_version"):
                state.outcome = "NEEDS_INPUT"
                state.errors.append("Provide a valid order and a return reason of 1–255 characters.")
                await ctx.yield_output(state)
                return
        state.order_total = float(result.get("total", state.order_total))
        state.refund_amount = state.order_total  # 估算值，并非已发放的退款
        state.order_revision = result.get("order_revision", "")
        state.requires_approval = result.get("requires_approval")
        state.outcome = result.get("outcome", "READY" if state.return_eligible else "REJECTED")
        state.completed_steps.append("check_eligibility")
        if state.operation_id and result.get("policy_version"):
            from shared.after_sales.service import get_operation

            receipt = await get_operation(state.operation_id, expected_payload_hash=payload_hash(intent))
            if receipt.get("operation_id") == state.operation_id and receipt.get("outcome") in {
                "SUCCEEDED",
                "REJECTED",
                "AWAITING_APPROVAL",
            }:
                state.existing_operation = receipt
                state.outcome = receipt["outcome"]
                if state.outcome == "SUCCEEDED":
                    state.return_id = receipt["return_id"]
                    state.refund_amount = receipt.get("refund_amount", 0)
                elif state.outcome == "AWAITING_APPROVAL":
                    state.hitl_requested = True
                else:
                    state.errors.append(receipt.get("message", "Return request was rejected."))
                await ctx.yield_output(state)
                return
        if not state.return_eligible:
            state.errors.append(result.get("reason", result.get("error", "Not eligible for return")))
            await ctx.yield_output(state)
            return
        await ctx.send_message(state)


class _InitiateReturnExecutor(Executor):
    def __init__(self, tools: dict) -> None:
        super().__init__(id="initiate-return")
        self._tools = tools

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        fn = self._tools.get("initiate_return")
        if not fn:
            state.errors.append("initiate_return tool not available")
            await ctx.yield_output(state)
            return
        approval_token = current_return_approval.set(
            ReturnApproval.from_dict(state.approval) if state.approval else None
        )
        operation_token = current_operation_id.set(state.operation_id)
        # 管理员可以代替请求归属人恢复该请求。归属人来自
        # 已持久化的检查点，而不是由模型提供。
        identity_token = None
        if current_user_role.get() == "admin" and state.user_email:
            identity_token = current_user_email.set(state.user_email)
        try:
            result = await fn(
                order_id=state.order_id,
                reason=state.reason or "Customer requested replacement",
                refund_method="store_credit",
            )
        except Exception:
            logger.exception("Return submission failed without a confirmed result")
            state.outcome = "UNKNOWN"
            state.errors.append("Return submission could not be confirmed. Check return status before retrying.")
            await ctx.yield_output(state)
            return
        finally:
            current_return_approval.reset(approval_token)
            current_operation_id.reset(operation_token)
            if identity_token is not None:
                current_user_email.reset(identity_token)

        if not isinstance(result, dict):
            state.outcome = "UNKNOWN"
            state.errors.append("Return tool returned an invalid result. Check operation status before retrying.")
            await ctx.yield_output(state)
            return
        state.outcome = result.get("outcome", "SUCCEEDED" if result.get("return_id") else "REJECTED")
        if "error" in result or not result.get("return_id") or result.get("success") is False:
            state.errors.append(
                f"initiate_return: {result.get('error', result.get('message', 'No confirmed return was created.'))}"
            )
            await ctx.yield_output(state)
            return

        try:
            amount = float(result["refund_amount"])
            if not math.isfinite(amount) or amount < 0:
                raise ValueError("invalid amount")
        except (KeyError, TypeError, ValueError):
            state.outcome = "UNKNOWN"
            state.errors.append("Return tool returned an invalid result. Check operation status before retrying.")
            await ctx.yield_output(state)
            return
        state.return_id = result.get("return_id")
        state.operation_id = result.get("operation_id", state.operation_id)
        state.refund_amount = amount
        state.completed_steps.append("initiate_return")
        await ctx.send_message(state)


class _SearchReplacementsExecutor(Executor):
    def __init__(self, tools: dict) -> None:
        super().__init__(id="search-replacements")
        self._tools = tools

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        fn = self._tools.get("search_products")
        if fn:
            try:
                results = await fn(
                    max_price=state.refund_amount * 1.2,
                    min_rating=4.0,
                    limit=5,
                )
                state.replacement_products = list(results) if isinstance(results, list) else []
                state.completed_steps.append("search_replacements")
            except Exception as exc:
                state.errors.append(f"search_replacements: {exc}")
        await ctx.send_message(state)


class _HitlGateExecutor(Executor):
    """针对高价值订单，通过 ``ctx.request_info`` 暂停工作流。"""

    def __init__(self, threshold: float) -> None:
        super().__init__(id="hitl-gate")
        self._threshold = threshold

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        state.completed_steps.append("hitl_gate")
        # 生产环境的资格校验会提供通用审批策略。较早的外部工具可能
        # 不提供它；此时仍以金额阈值作为兜底。
        required = (
            state.requires_approval if state.requires_approval is not None else state.order_total > self._threshold
        )
        if required:
            state.hitl_requested = True
            state.outcome = "AWAITING_APPROVAL"
            try:
                request = InitiateReturnInput(
                    order_id=state.order_id,
                    reason=state.reason or "Customer requested replacement",
                    refund_method="store_credit",
                )
                state.approval = ReturnApproval(
                    state.user_email,
                    payload_hash(request),
                    state.order_revision,
                    POLICY_VERSION,
                    (utc_now() + APPROVAL_TTL).isoformat(),
                ).to_dict()
            except ValueError:
                # 兼容非数据库工具。真正的提交服务会拒绝缺失/无效的授权
                # 以及非法参数。
                state.approval = None
            # 发出一个快照，好让观察事件流的调用方能在 request_info 事件
            # 暂停执行之前看到暂停状态。
            await ctx.yield_output(state)
            await ctx.request_info(
                ReturnApprovalRequest(
                    order_id=state.order_id,
                    order_total=state.order_total,
                    refund_amount=state.refund_amount,
                    replacement_count=len(state.replacement_products),
                    user_email=state.user_email,
                    reason=state.reason,
                    approval=state.approval,
                    operation_id=state.operation_id,
                ),
                response_type=bool,
            )
            return
        state.hitl_approved = True
        await ctx.send_message(state)

    @response_handler(request=ReturnApprovalRequest, response=bool)
    async def on_approval(
        self,
        original_request: ReturnApprovalRequest,
        response: bool,
        ctx: WorkflowContext[WorkflowState, WorkflowState],
    ) -> None:
        approved = bool(response)
        # 在执行第一次写入之前，保留精确的归属人、原因和策略绑定。
        # 保存该检查点时还不存在任何退货。
        resumed = WorkflowState(
            user_email=original_request.user_email,
            order_id=original_request.order_id,
            reason=original_request.reason,
            approval=original_request.approval,
            operation_id=original_request.operation_id,
            order_total=original_request.order_total,
            refund_amount=original_request.refund_amount,
            hitl_requested=True,
            hitl_approved=approved,
            completed_steps=["check_eligibility", "hitl_gate"],
        )
        if not approved:
            resumed.outcome = "REJECTED"
            if (
                original_request.operation_id
                and original_request.approval
                and original_request.approval.get("order_revision")
            ):
                from uuid import UUID

                from shared.after_sales.operations import reject_workflow

                intent = InitiateReturnInput(
                    order_id=resumed.order_id,
                    reason=resumed.reason or "Customer requested replacement",
                    refund_method="store_credit",
                )
                receipt = await reject_workflow(intent, UUID(original_request.operation_id))
                resumed.existing_operation = receipt
                resumed.outcome = receipt["outcome"]
                if receipt.get("success"):
                    resumed.return_id = receipt["return_id"]
                    await ctx.yield_output(resumed)
                    return
            resumed.errors.append("hitl_gate: return rejected by reviewer")
            await ctx.yield_output(resumed)
            return
        await ctx.send_message(resumed)


class _ApplyDiscountExecutor(Executor):
    def __init__(self, tools: dict) -> None:
        super().__init__(id="apply-discount")
        self._tools = tools

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        fn = self._tools.get("get_loyalty_tier")
        if fn:
            try:
                result = await fn()
                if float(result.get("discount_pct", 0)) > 0:
                    state.applied_discount = {
                        "tier": result.get("tier"),
                        "discount_pct": result.get("discount_pct"),
                    }
                state.completed_steps.append("apply_discount")
            except Exception as exc:
                state.errors.append(f"apply_discount: {exc}")
        else:
            state.completed_steps.append("apply_discount")
        await ctx.send_message(state)


class _FinalizeExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="finalize")

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[None, WorkflowState]) -> None:
        state.completed_steps.append("finalize")
        await ctx.yield_output(state)


# ─────────────────────── 公共 API ───────────────────────


class ReturnAndReplaceWorkflow:
    """基于 MAF 的顺序式退货工作流，带 HITL 审批闸门。

    用 tools 字典构造一次，之后可以按需多次调用 ``execute(state)``；
    每次调用在内部都会构建一个全新的 MAF 工作流。
    """

    def __init__(self, tools: dict) -> None:
        self._tools = tools

    def _build_maf_workflow(self):
        check = _CheckEligibilityExecutor(self._tools)
        initiate = _InitiateReturnExecutor(self._tools)
        search = _SearchReplacementsExecutor(self._tools)
        gate = _HitlGateExecutor(float(settings.RETURN_HITL_THRESHOLD))
        discount = _ApplyDiscountExecutor(self._tools)
        finalize = _FinalizeExecutor()

        return (
            WorkflowBuilder(start_executor=check, name="return-and-replace")
            .add_edge(check, gate)
            .add_edge(gate, initiate)
            .add_edge(initiate, search)
            .add_edge(search, discount)
            .add_edge(discount, finalize)
            .build()
        )

    async def execute(self, state: WorkflowState) -> WorkflowState:
        """运行工作流并返回最终的状态快照。

        当 HITL 闸门触发时，执行会暂停等待响应，并返回最近的状态快照
        （带有 ``hitl_requested=True`` 和 ``hitl_approved=None``）。
        """
        workflow = self._build_maf_workflow()

        final_state = state
        async for event in workflow.run(state, stream=True):
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, WorkflowState):
                    final_state = data

        # 与顺利路径的不变式保持一致：如果没有请求 HITL，
        # 则视为隐式通过审批。
        if not final_state.hitl_requested and final_state.hitl_approved is None and final_state.outcome == "SUCCEEDED":
            final_state.hitl_approved = True

        return final_state
