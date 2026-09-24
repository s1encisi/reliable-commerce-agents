"""让评测用例走真实的生产执行路径。

替代了 ``evaluator.py`` 中旧的 ``_run_agent()``——后者自己手写了一套 OpenAI
工具调用循环，并直接调用未加装饰器的原始工具函数，绕过了真实请求会经过的
每一个 ``AgentMiddleware``/``FunctionMiddleware``（护栏、人工参与
（Human-in-the-Loop，HITL），以及第二阶段的事实核验（grounding）校验）。

共有两条路径，因为 ``orchestrator/modes/`` 只是编排器层面的概念——
专业智能体从来不会被按模式分派，无论在评测中还是在生产环境中：

- ``orchestrator`` 用例走 ``orchestrator.modes.get_mode("tool")
  .run(...)``——与真实的 ``POST /api/chat`` 请求使用的是同一套分派。
- 五个专业智能体用例走 ``shared.agent_host._run_agent_native()``
  ——即每个专业智能体的 ``/message:send`` 处理器所调用的真实 A2A 入口。

两条路径都会运行智能体完整的 ``build_specialist_middleware()`` 中间件栈，
且都经由 ``shared.factory.get_chat_client()``——因此 ``LLM_PROVIDER=replay``
现在对评测真正可用了，而旧的循环从未做到这一点。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

from shared.agent_observability import get_steps, reset_steps
from shared.context import (
    current_session_id,
    current_user_email,
    current_user_role,
)
from shared.grounding.ledger import reset_grounding_ledger
from shared.guardrails.flags import get_guardrail_flags, reset_guardrail_flags
from shared.replay_client import ReplayFixtureMissingError

# 智能体工厂注册表——把评测用的智能体名称映射到它们的创建函数。
# 放在这里（而不是 run_evals.py）是因为 ProductionRunner 需要它来构建
# 专业智能体；run_evals.py 从这里导入它，供 CLI 的 --agent 选项使用，
# 从而避免 run_evals 与 harness 之间出现循环导入。
AGENT_FACTORIES: dict[str, tuple[str, str]] = {
    "product-discovery": ("product_discovery.agent", "create_product_discovery_agent"),
    "order-management": ("order_management.agent", "create_order_management_agent"),
    "pricing-promotions": ("pricing_promotions.agent", "create_pricing_promotions_agent"),
    "review-sentiment": ("review_sentiment.agent", "create_review_sentiment_agent"),
    "inventory-fulfillment": ("inventory_fulfillment.agent", "create_inventory_fulfillment_agent"),
    "orchestrator": ("orchestrator.agent", "create_orchestrator_agent"),
}


@dataclass
class RunOutcome:
    """一次生产运行产出的结果——足以支撑现有的每个评分器，也能支撑新的评分器。"""

    text: str
    tools_called: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    grounding: dict[str, Any] | None = None
    guardrail_flags: dict[str, bool] = field(default_factory=dict)
    error: str | None = None
    # 缺少回放夹具属于基础设施故障，而不是一次糟糕的回答。
    # 单独跟踪，这样 CI 就能说"有 3 个夹具缺失"，而不是报告一个得分为 0
    # 的智能体——正是这种歧义让 issue #25 跨两个 PR 一直悬而未决。
    fixture_missing: bool = False
    decision: dict[str, Any] | None = None
    usage_available: bool = False


def _create_agent(agent_name: str) -> Any:
    if agent_name not in AGENT_FACTORIES:
        available = ", ".join(sorted(AGENT_FACTORIES.keys()))
        raise ValueError(f"Unknown agent: {agent_name!r}. Available: {available}")
    module_path, factory_name = AGENT_FACTORIES[agent_name]
    module = importlib.import_module(module_path)
    return getattr(module, factory_name)()


def _tools_called(steps: list[dict[str, Any]]) -> list[str]:
    return [s.get("tool_name", "") for s in steps if s.get("tool_name")]


def _routes(steps: list[dict[str, Any]]) -> list[str]:
    routes: list[str] = []
    for s in steps:
        if s.get("tool_name") != "call_specialist_agent":
            continue
        tool_input = s.get("tool_input")
        agent_name = tool_input.get("agent_name") if isinstance(tool_input, dict) else None
        if agent_name:
            routes.append(agent_name)
    return routes


class ProductionRunner:
    """让单个评测输入针对某一个智能体走真实的生产路径。

    每个用例使用一个新的 runner（对同一个智能体也可以跨用例复用——
    专业智能体在首次使用时会被缓存，这与真实进程中每个智能体只构建一次、
    并在多个请求之间复用的方式一致）。
    """

    def __init__(
        self,
        agent_name: str,
        *,
        user_email: str = "eval@example.com",
        user_role: str = "customer",
        mode: str = "tool",
    ) -> None:
        self.agent_name = agent_name
        self.mode = mode
        self.user_email = user_email
        self.user_role = user_role
        self._agent: Any = None

    async def run(self, user_input: str) -> RunOutcome:
        current_user_email.set(self.user_email)
        current_user_role.set(self.user_role)
        current_session_id.set("")
        reset_steps()
        reset_grounding_ledger()
        reset_guardrail_flags()

        try:
            if self.agent_name == "orchestrator":
                outcome = await self._run_orchestrator(user_input)
            else:
                outcome = await self._run_specialist(user_input)
        except ReplayFixtureMissingError as exc:
            return RunOutcome(
                text="",
                error=str(exc),
                fixture_missing=True,
                guardrail_flags=get_guardrail_flags(),
            )
        except Exception as exc:
            # 专业智能体缺失夹具时，到达编排器的是一个 A2A 失败，而不是原始的
            # 异常类型，因此回退为按消息内容来识别。
            return RunOutcome(
                text="",
                error=str(exc),
                fixture_missing="No replay fixture" in str(exc),
                guardrail_flags=get_guardrail_flags(),
            )

        outcome.guardrail_flags = get_guardrail_flags()
        return outcome

    async def _run_orchestrator(self, user_input: str) -> RunOutcome:
        from orchestrator.modes import RunContext, get_mode

        mode = get_mode(self.mode)
        ctx = RunContext(history=[])
        text = ""
        grounding: dict[str, Any] | None = None
        usage: dict[str, Any] = {}
        payload: dict[str, Any] = {}

        async for event in mode.run(user_input, ctx):
            if event.kind == "run_completed":
                payload = event.payload
                text = event.payload.get("text", "")
                grounding = event.payload.get("grounding")
                usage = event.payload.get("usage") or {}

        steps = payload.get("steps") or get_steps()
        return RunOutcome(
            text=text,
            tools_called=_tools_called(steps),
            routes=_routes(steps) or [a for a in payload.get("agents_involved", []) if a != "orchestrator"],
            decision=payload.get("decision"),
            usage_available=bool(usage),
            tokens_in=usage.get("input_token_count") or 0,
            tokens_out=usage.get("output_token_count") or 0,
            grounding=grounding,
        )

    async def _run_specialist(self, user_input: str) -> RunOutcome:
        from shared.agent_host import _run_agent_native

        if self._agent is None:
            self._agent = _create_agent(self.agent_name)

        metadata_box: dict[str, Any] = {}
        text = await _run_agent_native(self._agent, user_input, metadata_box=metadata_box)

        steps = get_steps()
        usage = metadata_box.get("_maf_usage") or {}
        return RunOutcome(
            text=text,
            tools_called=_tools_called(steps),
            tokens_in=usage.get("input_token_count") or 0,
            tokens_out=usage.get("output_token_count") or 0,
            grounding=metadata_box.get("grounding"),
        )
