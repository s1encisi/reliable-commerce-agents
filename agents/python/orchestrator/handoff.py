"""编排器 → 专业智能体网格的 MAF 处理权交接工作流。

这是 ``tool`` 模式下 ``call_specialist_agent`` 工具路由的 ``handoff`` 替代方案。
``orchestrator/modes/handoff_mode.py`` 包装
:func:`build_orchestrator_handoff_workflow`，正是它让这里能从实际请求触达
—— 通过 ``/api/chat`` 上的 ``mode="handoff"``，或作为部署级默认值的
``ORCHESTRATION_MODE=handoff``。MAF 通过 Handoff 编排，在编排器与各专业
智能体之间机械地轮转发言。

每个专业智能体都是一个包装在 ``Agent`` 里的
:class:`~shared.remote_agent.RemoteSpecialistChatClient`，因此在网络上处理权
交接仍然走 A2A HTTP —— 机制是 Handoff，传输是 A2A。

默认仍保持基于工具（``ORCHESTRATION_MODE=tool``），因此本模块是增量式的；
除非某个请求或部署配置选择 ``handoff``，现有运行时的任何东西都不会改变。
"""

import logging
from typing import Any

from agent_framework import Agent
from agent_framework_orchestrations import HandoffBuilder

from shared.agent_factory import create_chat_client
from shared.config import settings
from shared.context import current_user_role
from shared.factory import parse_agent_registry
from shared.prompt_loader import load_prompt
from shared.remote_agent import make_remote_specialist_agent

logger = logging.getLogger(__name__)


def _load_registry() -> dict[str, str]:
    """处理权交接网格所用的专业智能体，取自经过校验的注册表。

    这里以前会捕获 JSON 错误、记一条警告然后返回 ``{}``。而一个没有专业
    智能体的处理权交接工作流仍会构建、仍会回答 —— 分诊智能体无处可交接，
    于是自己回复 —— 这就把一处配置笔误变成了「模型不再路由」。让它抛错吧。

    刻意不通过 ``get_agent_registry`` 缓存：测试与模式切换器都会在运行时
    修改 ``settings.AGENT_REGISTRY``，而 lru_cache 会永远返回第一个值。
    """
    return parse_agent_registry(settings.AGENT_REGISTRY)


def create_handoff_triage_agent() -> Agent:
    """处理权交接网格的起始智能体 —— 刻意不带任何工具。

    它不是 ``create_orchestrator_agent()``，而这个区别正是本模式能奏效的
    全部原因。两种模式以相反的机制路由：``tool`` 调用
    ``call_specialist_agent`` 并保留本轮的控制权；``handoff`` 调用一个由
    MAF 合成的交接工具并*转移*控制权。

    把工具路由编排器交给 ``HandoffBuilder``，会让它拥有两套相互竞争的
    路由机制，而它会用系统提示词里点名的那一套 —— 于是它永远不会调用
    交接工具。微软的指引明确指出这在这里是致命的：一个选择自己回应而不
    交接的智能体，会让工作流除了回到用户那里无处可去。在开启自主模式时，
    那会变成无界的自我延续循环。

    在本模块存在之前，曾对照真实技术栈实测：5,403 次流式更新、
    23,637 个字符、耗时 100-200 秒，且从未调用过任何专业智能体。
    """
    return Agent(
        client=create_chat_client(),
        name="orchestrator",
        description="Triage agent that routes the conversation to a specialist.",
        instructions=load_prompt("handoff-triage", current_user_role.get() or "customer"),
        # 不带工具，也不带 ECommerceContextProvider。两者的存在都是为了帮
        # 编排器*作答*；而本智能体唯一的职责是挑选专业智能体，它携带的每个
        # 工具都只是它除了交接之外还能多做的另一件事。
        require_per_service_call_history_persistence=True,
    )


def build_remote_specialist_agents(registry: dict[str, str] | None = None) -> list[Agent]:
    """把 AGENT_REGISTRY 映射转换为一组兼容 Handoff 的 Agent。"""
    reg = registry if registry is not None else _load_registry()
    return [make_remote_specialist_agent(name, url) for name, url in reg.items()]


def build_orchestrator_handoff_workflow(
    *,
    orchestrator: Agent | None = None,
    specialists: list[Agent] | None = None,
    autonomous_mode: bool | None = None,
) -> Any:
    """构建一个 MAF HandoffBuilder 工作流。

    Args:
        orchestrator: 可选，预先构建好的编排器智能体。省略时会用标准系统
            提示词创建一个。
        specialists: 可选，预先构建好的专业智能体。省略时它们由
            ``settings.AGENT_REGISTRY`` 推导得到。
        autonomous_mode: 覆盖 ``settings.HANDOFF_AUTONOMOUS_MODE``。
            为 ``True`` 时，专业智能体自动回复，无需中间的用户轮次；
            为 ``False`` 时，每次交接都会在工作流流中发出一个可观测事件。
    """
    orchestrator = orchestrator or create_handoff_triage_agent()
    specialists = specialists if specialists is not None else build_remote_specialist_agents()
    auto = settings.HANDOFF_AUTONOMOUS_MODE if autonomous_mode is None else autonomous_mode

    builder = HandoffBuilder(name="orchestrator-handoff").participants([orchestrator, *specialists])
    builder = builder.with_start_agent(orchestrator)

    # 网格拓扑：编排器可交接给任意专业智能体；每个专业智能体都能交回给
    # 编排器。这里刻意不让专业智能体之间互相交接 —— 那条串话路径已经能
    # 通过编排器往返实现，引入它会让路由图从客服运维视角来看难懂得多。
    if specialists:
        builder = builder.add_handoff(orchestrator, specialists)
        for specialist in specialists:
            builder = builder.add_handoff(specialist, [orchestrator])

    if auto:
        # 让分诊智能体在专业智能体回复后继续持有发言权，这样它就能决定
        # 再次交接（或收尾），而不必每轮都把对话弹回给终端用户。
        #
        # 轮次上限是安全网，且不是可选项。自主模式的契约是「当智能体不交接时，
        # 给它一个延续提示词并再次运行它」—— 于是一个*无法*交接的智能体
        # 会一直跑到有东西阻止它为止。默认是 50 轮，按每轮约 450 字符计算，
        # 正是本模式过去产出的那篇 23,000 字符的独白。三轮足够完成
        # 交接、交回、收尾。
        builder = builder.with_autonomous_mode(
            agents=[orchestrator],
            turn_limits={orchestrator.name: settings.HANDOFF_MAX_TURNS},
        )

    return builder.build()
