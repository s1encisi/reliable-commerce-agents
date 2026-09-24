"""可靠电商多智能体平台可复用的 MAF 中间件。

AgentRunLogger 记录运行耗时与关联标识；ToolAuditMiddleware 审计
工具调用；PiiRedactionMiddleware 在模型调用前遮蔽敏感模式。
各模块提供日志和轻量计数器，除非明确需要共享状态，否则保持运行间无状态。
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework._middleware import (
    AgentContext,
    AgentMiddleware,
    ChatContext,
    ChatMiddleware,
    FunctionInvocationContext,
    FunctionMiddleware,
)

from shared.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────── Agent-run logging ───────────────────────


class AgentRunLogger(AgentMiddleware):
    """记录每次智能体调用的开始与结束。

    生成短关联标识并写入 context.metadata["run_id"]，供后续日志关联。
    """

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        from shared.paid_transport import current_root_run, current_run_deadline

        root_token = current_root_run.set(current_root_run.get() or str(uuid.uuid4()))
        deadline_token = current_run_deadline.set(
            current_run_deadline.get() or time.time() + settings.MAF_STREAM_TIMEOUT_SECONDS
        )
        run_id = current_root_run.get()[:8]
        agent_name = getattr(getattr(context, "agent", None), "name", "agent") or "agent"
        if hasattr(context, "metadata") and isinstance(context.metadata, dict):
            context.metadata.setdefault("run_id", run_id)
            context.metadata.setdefault("root_run_id", current_root_run.get())

        start = time.perf_counter()
        logger.info("agent.start agent=%s run_id=%s", agent_name, run_id)
        try:
            await call_next()
        except Exception:
            elapsed = (time.perf_counter() - start) * 1000
            logger.exception(
                "agent.fail agent=%s run_id=%s elapsed_ms=%.1f",
                agent_name,
                run_id,
                elapsed,
            )
            raise
        else:
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(
                "agent.finish agent=%s run_id=%s elapsed_ms=%.1f",
                agent_name,
                run_id,
                elapsed,
            )

        finally:
            current_root_run.reset(root_token)
            current_run_deadline.reset(deadline_token)


# ─────────────────────── Tool audit ───────────────────────


class ToolAuditMiddleware(FunctionMiddleware):
    """审计工具名、调用者、延迟及成功状态。

    本中间件只记录行为；审批由 MAF 门控及项目的 HITL 执行路径负责。
    """

    def __init__(self, *, capture_arguments: bool = False) -> None:
        self.capture_arguments = capture_arguments
        self.audited: list[dict[str, Any]] = []

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        fn = getattr(context, "function", None)
        name = getattr(fn, "name", None) or getattr(fn, "__name__", "unknown")
        start = time.perf_counter()
        error: str | None = None
        try:
            await call_next()
        except Exception as exc:  # pragma: no cover - exercised via unit tests
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            record: dict[str, Any] = {
                "tool": name,
                "elapsed_ms": round(elapsed, 2),
                "error": error,
            }
            from shared.after_sales.contracts import Outcome

            result = getattr(context, "result", None)
            if isinstance(result, dict):
                outcome = result.get("outcome")
                record["business_outcome"] = (
                    outcome if isinstance(outcome, str) and outcome in {s.value for s in Outcome} else None
                )
                record["business_success"] = result.get("success") if isinstance(result.get("success"), bool) else None
            if self.capture_arguments:
                args = getattr(context, "arguments", None)
                if isinstance(args, dict):
                    record["arguments"] = dict(args)
            self.audited.append(record)
            logger.info(
                "tool.invoked name=%s elapsed_ms=%.1f error=%s business_outcome=%s business_success=%s",
                name,
                elapsed,
                error or "-",
                record.get("business_outcome"),
                record.get("business_success"),
            )


# ─────────────────────── PII redaction ───────────────────────


_UUID_PATTERN = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_CARD_PATTERN = re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b")
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


class PiiRedactionMiddleware(ChatMiddleware):
    """模型调用前遮蔽银行卡号和美国社会安全号形态的字符串。

    记录脱敏次数供监测。规则集合较小，扩展前应核对匹配范围和误报。
    """

    CARD_MASK = "[REDACTED-CARD]"
    SSN_MASK = "[REDACTED-SSN]"

    def __init__(self) -> None:
        self.redactions = 0

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        messages = getattr(context, "messages", None) or []
        for message in messages:
            for content in getattr(message, "contents", []) or []:
                text = getattr(content, "text", None)
                if not text or not isinstance(text, str):
                    continue
                identifiers = [match.span() for match in _UUID_PATTERN.finditer(text)]
                cards = 0

                def redact_card(match: re.Match) -> str:
                    nonlocal cards
                    if any(start <= match.start() and match.end() <= end for start, end in identifiers):
                        return match.group(0)
                    cards += 1
                    return self.CARD_MASK

                redacted = _CARD_PATTERN.sub(redact_card, text)
                # UUID 片段也可能匹配 SSN；只在标识符之外脱敏。
                identifiers = [match.span() for match in _UUID_PATTERN.finditer(redacted)]
                ssns = 0

                def redact_ssn(match: re.Match) -> str:
                    nonlocal ssns
                    if any(start <= match.start() and match.end() <= end for start, end in identifiers):
                        return match.group(0)
                    ssns += 1
                    return self.SSN_MASK

                redacted = _SSN_PATTERN.sub(redact_ssn, redacted)
                if cards + ssns:
                    self.redactions += cards + ssns
                    try:
                        content.text = redacted  # type: ignore[attr-defined]
                    except AttributeError:
                        # 兼容内容对象不可变的情形。
                        logger.warning("could not redact content of type %s", type(content).__name__)
        await call_next()


# ─────────────────────── Factory ───────────────────────


def default_middleware_stack() -> list[Any]:
    """专业智能体默认中间件栈。

    顺序有意义：运行日志包裹整体执行，审计拦截每个工具，
    个人信息脱敏在聊天客户端之前执行。
    """
    return [
        AgentRunLogger(),
        ToolAuditMiddleware(),
        PiiRedactionMiddleware(),
    ]


def build_specialist_middleware(*, include_steps: bool = True) -> list[Any]:
    """专业智能体和编排器共用的中间件组装入口。

    MAF 按类型将列表分派到智能体、聊天和函数管线，覆盖运行日志、
    工具审计、注入检测、个人信息脱敏、输出净化、事实台账与核验、
    费用预算、内容审核和执行步骤记录。

    GUARDRAILS_ENABLED 控制护栏，个人信息脱敏独立保留。事实核验、
    费用预算与内容审核分别由各自 MODE 开关控制，off 时不挂载。
    """
    # 按需导入以避免循环依赖。
    # 护栏依赖 shared.config，而智能体很早就会导入中间件，
    # 因此保持依赖方向清晰。
    from shared.agent_observability import STEP_MIDDLEWARE
    from shared.execution_policy import ReadOnlyToolMiddleware
    from shared.grounding.ledger import GROUNDING_LEDGER_MIDDLEWARE
    from shared.grounding.middleware import GroundingVerificationMiddleware
    from shared.guardrails.cost_budget_middleware import CostBudgetMiddleware
    from shared.guardrails.injection_middleware import InjectionDetectionChatMiddleware
    from shared.guardrails.moderation_middleware import OutputModerationMiddleware
    from shared.guardrails.output_middleware import OutputSanitizationMiddleware
    from shared.hitl import HITLFunctionMiddleware

    stack: list[Any] = [AgentRunLogger(), ReadOnlyToolMiddleware(), ToolAuditMiddleware()]
    if settings.GUARDRAILS_ENABLED:
        stack.append(InjectionDetectionChatMiddleware())
    stack.append(PiiRedactionMiddleware())
    if settings.GUARDRAILS_ENABLED:
        stack.append(OutputSanitizationMiddleware())
    if settings.HITL_ENABLED:
        stack.append(HITLFunctionMiddleware())
    if settings.GROUNDING_MODE != "off":
        stack.extend(GROUNDING_LEDGER_MIDDLEWARE)
        stack.append(GroundingVerificationMiddleware())
    if settings.COST_BUDGET_MODE != "off":
        stack.append(CostBudgetMiddleware())
    if settings.OUTPUT_MODERATION_MODE != "off":
        stack.append(OutputModerationMiddleware())
    if include_steps:
        stack.extend(STEP_MIDDLEWARE)
    return stack
