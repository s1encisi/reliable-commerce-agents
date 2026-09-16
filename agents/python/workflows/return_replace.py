"""Return and Replace Workflow — MAF Sequential orchestration with HITL gate.

Step chain: check-eligibility → hitl-gate → initiate-return →
search-replacements → apply-discount → finalize.

The eligibility service supplies the common approval policy: approval is
required when HITL is enabled or the trusted order total exceeds
``settings.RETURN_HITL_THRESHOLD``. The gate emits a bound snapshot through
``ctx.request_info`` before any return is created. Resume restores the owner
and exact parameters; the shared service checks current eligibility again.

Refactored from a custom sequential state machine to a MAF
``WorkflowBuilder`` per ``plans/refactor/09-return-replace-sequential-hitl.md``.
Public API — class, dataclass, ``execute(state) -> state`` signature — is
preserved so callers don't have to change.

Note: do NOT add ``from __future__ import annotations`` here. MAF's
``@response_handler`` resolves parameter types via ``inspect.signature``
at import time; stringified annotations break that resolution.
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
    """Carries the in-flight workflow state from executor to executor."""

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

    # Populated along the chain
    return_eligible: bool = False
    return_id: "str | None" = None
    refund_amount: float = 0.0
    replacement_products: list = field(default_factory=list)
    applied_discount: "dict | None" = None

    # HITL state
    hitl_requested: bool = False
    hitl_approved: "bool | None" = None

    # Execution tracking
    completed_steps: list = field(default_factory=list)
    errors: list = field(default_factory=list)


@dataclass
class ReturnApprovalRequest:
    """Payload emitted by the HITL gate for high-value returns."""

    order_id: str
    order_total: float
    refund_amount: float
    replacement_count: int
    user_email: str = ""
    reason: str = ""
    approval: "dict | None" = None
    operation_id: "str | None" = None


# ─────────────────────── Executors ───────────────────────


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
        state.refund_amount = state.order_total  # estimate, not an issued refund
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
        # An admin may resume a request on its owner's behalf. The owner comes
        # from the persisted checkpoint; it is not supplied by the model.
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
    """Pauses the workflow via ``ctx.request_info`` for high-value orders."""

    def __init__(self, threshold: float) -> None:
        super().__init__(id="hitl-gate")
        self._threshold = threshold

    @handler
    async def run(self, state: WorkflowState, ctx: WorkflowContext[WorkflowState, WorkflowState]) -> None:
        state.completed_steps.append("hitl_gate")
        # Production eligibility supplies the common approval policy. Older
        # external tools may omit it; the value threshold remains a fallback.
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
                # Compatibility with non-DB tools. The real submission service
                # refuses absent/invalid authorization and invalid arguments.
                state.approval = None
            # Emit a snapshot so callers observing the stream can see the
            # pause state before the request_info event pauses execution.
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
        # Preserve the exact owner, reason and policy binding before executing
        # the first write. No return existed when this checkpoint was saved.
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


# ─────────────────────── Public API ───────────────────────


class ReturnAndReplaceWorkflow:
    """MAF-backed sequential return workflow with HITL approval gate.

    Construct once with the tools dict, then call ``execute(state)`` as
    many times as you like; each call builds a fresh MAF workflow under
    the covers.
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
        """Run the workflow and return the final state snapshot.

        When the HITL gate fires, execution pauses waiting for a response
        and the most recent state snapshot is returned (with
        ``hitl_requested=True`` and ``hitl_approved=None``).
        """
        workflow = self._build_maf_workflow()

        final_state = state
        async for event in workflow.run(state, stream=True):
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, WorkflowState):
                    final_state = data

        # Mirror the happy-path invariant: if no HITL was requested,
        # treat the run as implicitly approved.
        if not final_state.hitl_requested and final_state.hitl_approved is None and final_state.outcome == "SUCCEEDED":
            final_state.hitl_approved = True

        return final_state
