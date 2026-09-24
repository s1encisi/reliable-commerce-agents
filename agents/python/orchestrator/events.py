"""归一化的编排事件协议。

模式注册表（``orchestrator/modes/``，Phase 1.2）背后将存在五种编排机制：
普通工具路由、MAF 的 ``HandoffBuilder``、MAF ``WorkflowBuilder`` 图
（扇出/扇入、声明式 YAML），以及最终的 magentic 管理器。每种机制都发出
自己的原生事件形状 —— 工作流是带 18 个取值 ``type`` 字面量的
``WorkflowEvent``，工具路由是手工构造的步骤字典（见
``shared/agent_observability.py``），处理权交接又是另一种。Web UI 不应
知道当前跑的是哪种机制；它只渲染一种形状。

``OrchestrationEvent`` 就是那种形状。``adapt_workflow_event()`` 与
``adapt_step()`` 是目前存在的两个适配器，把当前真正接入到任何东西的
两个事件源（工作流测试，以及实时的步骤记录器路径）转换到这套通用协议。
针对处理权交接专用与 magentic 专用事件类型的适配器，会在这些模式真正
接入实时应用（Phase 1.2）时补充 —— 下面已经为它们预留了 "handoff" 与
"delta" 的 ``kind`` 取值，但确切的载荷形状更适合对照真实接线来设计，
而不是现在猜测。

目前还没有任何东西消费本模块。它是 Phase 1.2 模式注册表与 Phase 1.4
新增的 SSE 帧的脚手架 —— 保持导入开销低、无依赖（不含 FastAPI、不含 DB），
以便能从任何地方导入而不牵入编排器的其余部分。
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

EventKind = Literal[
    "run_started",
    "graph",
    "node_enter",
    "node_exit",
    "edge",
    "handoff",
    "tool_call",
    "delta",
    "checkpoint",
    "request_info",
    "grounding",
    "run_completed",
    "error",
]


class OrchestrationEvent(BaseModel):
    """每个编排模式发出的归一化事件流中的一帧。

    ``node_id`` 标识模式图中的一个步骤（工作流模式中是执行器 id，
    工具路由 / 处理权交接网格中是智能体名）—— 对不按图划分的事件
    （``run_started``、``run_completed``）为 None。``payload`` 刻意是一个
    开放的 dict，而不是各类型化载荷的联合：每种 ``kind`` 都有自己的形状
    （各自实际携带什么见下方适配器），而在扇出/扇入、处理权交接与
    magentic 事件之间强行统一 schema，要么会得到一个非常宽的联合，
    要么会丢失信息。消费者对 ``kind`` 做分支并据此知道 ``payload`` 里
    有什么，正如 SSE 帧消费者已经在对帧的 ``event:`` 名做分支一样。
    """

    kind: EventKind
    node_id: str | None = None
    agent: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    ts_ms: int = Field(default_factory=lambda: int(time.monotonic() * 1000))


def _jsonable(value: Any) -> Any:
    """对最终进入 ``payload`` 的值做尽力而为的 JSON 安全转换。

    工作流事件的 ``data`` 常常是 dataclass 或 Pydantic 模型（例如
    ``ReturnApprovalRequest``、``WorkflowState``）而不是普通 dict ——
    已对照一次真实的 ``return_replace`` 工作流运行直接验证过。
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if hasattr(value, "to_dict"):
        try:
            return _jsonable(value.to_dict())
        except Exception:
            pass
    return str(value)


# 不携带任何执行器级、对 UI 有意义信息的事件类型 ——
# 已对照一次真实工作流运行（return_replace）验证过："started"/"status"
# 只是把整次运行括起来，.data 与 .executor_id 里都没有东西，而
# "superstep_started"/"superstep_completed" 是 MAF 在扇出/扇入轮次之间的
# 内部屏障记账，不是读者能识别出的图节点。丢掉它们，能让归一化流对每件
# 观察者真正关心的事只保留一帧，与现有步骤记录器流只在工具调用时发出
# （而非每次内部状态转换都发出）的做法保持一致。
_SILENT_TYPES = frozenset({"status", "superstep_started", "superstep_completed"})


def adapt_workflow_event(event: Any) -> OrchestrationEvent | None:
    """把一个 MAF ``WorkflowEvent`` 转换为归一化协议。

    对没有 UI 相关信息可说的事件类型返回 None（见 ``_SILENT_TYPES``）——
    此时调用方应跳过产出一帧，而不是合成一个空帧。
    """
    etype = getattr(event, "type", None)
    if etype in _SILENT_TYPES:
        return None

    data = getattr(event, "data", None)
    executor_id = getattr(event, "executor_id", None)

    if etype == "started":
        return OrchestrationEvent(kind="run_started", payload={})

    if etype == "executor_invoked":
        return OrchestrationEvent(kind="node_enter", node_id=executor_id, payload={"data": _jsonable(data)})

    if etype == "executor_completed":
        return OrchestrationEvent(kind="node_exit", node_id=executor_id, payload={"data": _jsonable(data)})

    if etype == "executor_bypassed":
        return OrchestrationEvent(
            kind="node_exit", node_id=executor_id, payload={"data": _jsonable(data), "bypassed": True}
        )

    if etype == "executor_failed":
        return OrchestrationEvent(kind="error", node_id=executor_id, payload={"data": _jsonable(data)})

    if etype == "request_info":
        # source_executor_id 只在 request_info 事件上读取才有效 ——
        # 在其他任何类型上访问，WorkflowEvent 都会抛 RuntimeError。
        source = getattr(event, "source_executor_id", None)
        request_id = getattr(event, "request_id", None)
        request_type = getattr(event, "request_type", None)
        response_type = getattr(event, "response_type", None)
        return OrchestrationEvent(
            kind="request_info",
            node_id=source,
            payload={
                "request_id": request_id,
                "request_type": getattr(request_type, "__name__", None),
                "response_type": getattr(response_type, "__name__", None),
                "data": _jsonable(data),
            },
        )

    if etype == "handoff_sent":
        return OrchestrationEvent(kind="handoff", node_id=executor_id, payload={"data": _jsonable(data)})

    if etype in ("output", "intermediate", "data", "group_chat", "magentic_orchestrator"):
        return OrchestrationEvent(kind="delta", node_id=executor_id, payload={"type": etype, "data": _jsonable(data)})

    if etype in ("warning", "error", "failed"):
        return OrchestrationEvent(kind="error", node_id=executor_id, payload={"type": etype, "data": _jsonable(data)})

    # 向前兼容的默认分支：把未映射的事件类型作为 delta 呈现，而不是静默
    # 丢弃 —— 未来某个 MAF 版本新增的 WorkflowEventType 应当可见（哪怕没有
    # 专属样式），而不是不可见。
    return OrchestrationEvent(kind="delta", node_id=executor_id, payload={"type": etype, "data": _jsonable(data)})


def delta_text(payload: dict[str, Any]) -> str:
    """从 ``kind="delta"`` 事件的载荷中提取用户可见的助手文本。

    ``adapt_workflow_event`` 会把工作流原始的 ``AgentResponseUpdate`` 形状的
    ``data`` 包装成 ``{"type": ..., "data": {"contents": [...], "role": ..., ...}}``
    —— 已对照一次真实的处理权交接运行验证过（见
    ``orchestrator/modes/handoff_mode.py`` 自己的文本拼装逻辑，它读取的是
    ``_jsonable`` 之前的同一形状）。只有 type 为 ``"text"`` 的 ``contents``
    条目才是真正的显示文本 —— ``"function_call"``/``"function_result"``
    内容条目是工具机制（例如合成的 ``handoff_to_x`` 调用），绝不能以原始
    JSON 的形式泄漏进聊天气泡。
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        return ""
    contents = data.get("contents")
    if not isinstance(contents, list):
        return ""
    return "".join(c.get("text", "") for c in contents if isinstance(c, dict) and c.get("type") == "text")


def adapt_step(step: dict[str, Any]) -> OrchestrationEvent:
    """把步骤记录器 dict（``shared/agent_observability.py``）转换为
    归一化协议。

    步骤 dict 始终是良构的 —— ``StepRecorderMiddleware`` 用固定的键集
    （``tool_name``、``tool_input``、``tool_output``、``status``、
    ``duration_ms``）构建它们，而 ``agent`` 由调用方在本函数运行前设置
    （``routes.py`` 的 ``s.setdefault("agent", "orchestrator")``）——
    因此本适配器直接做普通的 ``.get()`` 读取，而不需要
    ``adapt_workflow_event`` 为异构 MAF 对象所需的那些防御性 ``getattr`` 链。
    """
    return OrchestrationEvent(
        kind="tool_call",
        node_id=step.get("tool_name"),
        agent=step.get("agent"),
        payload={
            "tool_input": step.get("tool_input"),
            "tool_output": step.get("tool_output"),
            "status": step.get("status"),
            "duration_ms": step.get("duration_ms"),
        },
    )
