"""聊天路由 —— ``/api/chat``、``/api/chat/stream``。

从 Phase 1 之前的单体 ``routes.py``（现为 ``.legacy``）中拆分出来，
因为聊天是每种编排模式都会触及的那一条路由。``chat()`` 现在通过
``orchestrator.modes.get_mode()`` 分发（模式优先级：请求体 ``mode`` ->
``settings.ORCHESTRATION_MODE`` -> 注册表默认值），而不是直接调用工具路由
智能体。

``chat_stream()`` 按解析出的模式分支：``tool`` 保持其原有的直接智能体
流式路径不变（``ToolRouterMode.run()`` 会在产出任何内容之前等待完整响应，
因此把默认模式也走注册表，会把真正的增量流式变成一次运行结束时的整块
倾倒）。其他每个模式都直接通过 ``get_mode(...).run()`` 流式输出。其中有些
—— ``handoff`` —— 携带真正的增量文本（已对照一次真实运行验证：``"output"``
事件类型经 ``adapt_workflow_event`` 映射为 ``kind="delta"``，其数据是
``AgentResponseUpdate`` 形状，``events.delta_text()`` 能读取）。其他则不能：
扇出/扇入与顺序式 MAF 工作流（``workflow:pre-purchase``、
``workflow:return-replace``、``group-chat``）大多在执行器之间调用
``ctx.send_message()``，那完全不会产生 delta，而它们唯一一次
``ctx.yield_output()`` 携带的是原始状态 dataclass，而非
``AgentResponseUpdate`` 内容 —— 已对照一次真实的购前调研运行验证。对于这些
模式，``_run_mode_task()`` 会在没有任何增量流式内容时退而把 ``run_completed``
的完整文本作为一个分块推送，因此无论哪种方式，每个模式都能保证有非空的
可见响应。

历史记录（Phase 1.5）：两个端点都通过
``shared.session.get_history_as_dicts`` + ``get_history_provider`` 读取对话
历史，而不是手工拼 ``SELECT ... FROM messages``。现在读取发生在当前轮的
用户消息被插入*之前* —— 旧顺序（先插入、再查询）会让刚插入的那一行出现在
``history`` 里，又被 ``shared/agent_host.py::_history_as_maf_messages``
追加第二次，而后者总是自己追加当前消息。消息的*写入*未做改动：通用的
``HistoryProvider.save_messages()`` 只持久化 role/content，不持久化本应用
自己那条 INSERT 为时间线 UI 携带的
``agent_name``/``agents_involved``/``metadata`` —— 已直接验证：把
``HistoryProvider`` 作为自动的 ``context_providers=[...]`` 钩子挂上去，
会在每轮自动保存并把行重复，因此刻意不那样接线。
"""

from __future__ import annotations

import asyncio as _asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from shared.after_sales.http import operation_scope
from shared.agent_observability import get_steps, reset_steps
from shared.context import current_session_id
from shared.db import get_pool
from shared.grounding.ledger import reset_grounding_ledger
from shared.rate_limit import rate_limit_chat
from shared.session import get_history_as_dicts, get_history_provider
from shared.sse import encode_sse
from shared.usage_db import UsageTimer, log_agent_usage, log_execution_step

from .legacy import optional_auth

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    mode: str | None = None


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    agents_involved: list[str]
    grounding: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    outcome: str | None = None


_PENDING_PERSIST_TASKS: set[_asyncio.Task] = set()


def _bind_session_to_conversation(conversation_id: str | None) -> None:
    """把 ``current_session_id`` 指向本轮所属的会话（#9）。

    每次 A2A 调用都会读取这个 ContextVar ——
    shared/oauth/service_client.py 的 ``build_a2a_headers()`` 把它作为
    ``x-session-id`` 发送，而它是唯一告诉专业智能体该从哪个会话重新水合
    的东西（``shared/agent_host.py::_rehydrate_history_from_session``）。

    除此之外，它只在恰好四个地方被设置，而每一处都是读取入站的
    ``x-session-id`` *请求头*。浏览器从不发送那个头，因此转发给专业智能体的
    值始终是 ``""`` —— 而重新水合会在接触数据库之前就因空值短路，且不留日志。
    于是专业智能体对每一个源自浏览器的追问，都是从一张白纸开始作答。

    这属于这里，而不属于 ``optional_auth``/``require_auth``，因为只有这一层
    知道会话 id：它随请求*体*抵达，而且常常是上面几行刚创建的，而非由客户端
    提供。

    匿名请求时调用方必须传 ``None``。``body.conversation_id`` 由客户端提供，
    且只在已认证路径上做过归属校验，因此为匿名调用方绑定它，会让任何人都能
    凭 UUID 重新水合任意会话 —— 专业智能体的重新水合查询信任这个 id。
    况且匿名聊天本来就没有自己的会话行。
    """
    if conversation_id:
        current_session_id.set(conversation_id)


def _spawn_persist_task(coro: Any) -> _asyncio.Task:
    """以「发射后不管」的方式运行一个持久化协程，使其不受父级
    请求/生成器自身取消的影响。

    客户端在流式过程中断开，会直接取消 SSE 生成器的任务（Starlette 自己的
    ASGI 层断开处理，与本模块自己的 ``request.is_disconnected()`` 轮询是
    两回事）—— 没有这个机制的话，那次取消会直接越过持久化代码传播出去，
    静默丢弃助手已经生成的响应（#10）。这里在模块级 set 中保留一个强引用，
    因为 asyncio 对裸的 ``create_task()`` 结果只持有弱引用 —— 未被引用的
    任务可能在完成前就被垃圾回收。
    """
    task = _asyncio.create_task(coro)
    _PENDING_PERSIST_TASKS.add(task)
    task.add_done_callback(_PENDING_PERSIST_TASKS.discard)
    return task


async def _persist_assistant_turn(
    *,
    pool: Any,
    conversation_id: str,
    response_text: str,
    agents_involved: list[str],
    steps: list[dict[str, Any]],
    user_id: str,
    user_email: str,
    body_message: str,
    start_time: float,
    run_payload_box: dict[str, Any],
    stream_usage: dict[str, Any],
    disconnected: bool,
) -> None:
    """为一次 chat_stream 轮次持久化助手的消息 + 时间线。

    以游离任务的方式运行（见 ``_spawn_persist_task``），这样客户端在流式
    过程中断开就不会丢失智能体已经生成的响应。
    """
    try:
        # `_live` 是某个步骤已被流式发送时设置的传输标记
        # （orchestrator/agent.py）。正常路径会在决定要重发什么时把它弹出；
        # 断开路径从不运行那次排空，因此这里也要剥掉它 —— 持久化的元数据
        # 应当描述这次运行，而不是它的帧是如何投递的。
        for step in steps:
            step.pop("_live", None)
        metadata = {"steps": steps[:50], "agents_involved": agents_involved}
        await pool.execute(
            """INSERT INTO messages (conversation_id, role, content, agent_name, agents_involved, metadata)
               VALUES ($1, 'assistant', $2, 'orchestrator', $3, $4::jsonb)""",
            conversation_id,
            response_text,
            agents_involved,
            json.dumps(metadata, default=str),
        )
        await pool.execute(
            "UPDATE conversations SET last_message_at = NOW() WHERE id = $1",
            conversation_id,
        )
        duration_ms = int((time.monotonic() - start_time) * 1000)
        usage_log_id = await log_agent_usage(
            user_id=user_id,
            agent_name="orchestrator",
            session_id=conversation_id,
            input_summary=body_message,
            duration_ms=duration_ms,
            tool_calls_count=len(steps),
            tokens_in=stream_usage.get("input_token_count") or 0,
            tokens_out=stream_usage.get("output_token_count") or 0,
        )
        if usage_log_id:
            for idx, s in enumerate(steps):
                ti = s.get("tool_input")
                to = s.get("tool_output")
                await log_execution_step(
                    usage_log_id=usage_log_id,
                    step_index=idx,
                    tool_name=f"{s.get('agent', 'orchestrator')}:{s.get('tool_name', 'tool')}",
                    tool_input=ti if isinstance(ti, dict) else {"value": ti},
                    tool_output=to if isinstance(to, dict) else {"value": to},
                    status=s.get("status", "success"),
                    duration_ms=s.get("duration_ms", 0),
                )
        # 流需要这个 id 来告诉客户端它刚看完的是哪次运行 —— 没有它，聊天内的
        # 审批就无处可 POST。它无法更早发送：usage_logs 行正是在这里创建的，
        # 比 `metadata` 已经发出的时间晚了好几帧。
        run_payload_box["usage_log_id"] = usage_log_id
        await _link_run_artifacts(pool, usage_log_id, user_email, run_payload_box)
        if run_payload_box.get("decision", {}).get("context_enabled"):
            from shared.context_pipeline import remember_evidence

            await remember_evidence(conversation_id, steps)
        if disconnected:
            logger.info(
                "chat_stream.persisted_after_disconnect conversation=%s chars=%d",
                conversation_id,
                len(response_text),
            )
    except Exception:
        logger.exception("chat_stream.persist_error conversation=%s disconnected=%s", conversation_id, disconnected)


async def _link_run_artifacts(pool: Any, usage_log_id: Any, user_email: str, run_payload: dict[str, Any]) -> None:
    """把一次运行的检查点 + 人工参与暂停（若有）关联回它的 ``usage_logs``
    行 —— 这正是 ``GET /api/runs/{id}/checkpoints`` 与
    ``POST /api/orchestration/{run_id}/resume`` 日后找到它们所需要的。

    对不设置这些载荷键的每种模式都是空操作（``tool``、``handoff``、
    已完成的 ``workflow:pre-purchase``/``group-chat`` 运行，或未暂停的
    ``workflow:return-replace`` 运行）—— 在做任何事之前先检查
    ``usage_log_id`` 与 ``latest_checkpoint_id``，就能在没有模式名分支的
    情况下覆盖所有这些情况。
    """
    if not usage_log_id:
        return
    checkpoint_id = run_payload.get("latest_checkpoint_id")
    if checkpoint_id:
        await pool.execute(
            "UPDATE workflow_checkpoints SET usage_log_id = $1 WHERE checkpoint_id = $2",
            usage_log_id,
            checkpoint_id,
        )
    if run_payload.get("pending_approval") and run_payload.get("request_id"):
        from uuid import UUID

        from shared.after_sales import operations
        from shared.after_sales.approval import payload_hash
        from shared.after_sales.policy import POLICY_VERSION
        from shared.tool_inputs import InitiateReturnInput

        async with pool.acquire() as conn:
            async with conn.transaction():
                intent_data = run_payload.get("return_intent")
                op_id = run_payload.get("operation_id")
                op = None
                if intent_data and op_id:
                    intent = InitiateReturnInput.model_validate(intent_data)
                    await operations.reserve_on(conn, intent, UUID(op_id))
                    op = await operations.load(conn, UUID(op_id), lock="update")
                    if op is None:
                        raise ValueError("Workflow order is no longer accessible")
                    if op["payload_hash"] != payload_hash(intent):
                        run_payload.update(
                            pending_approval=False,
                            outcome="REJECTED",
                            text="This operation ID belongs to different parameters.",
                        )
                        return
                    if op["status"] in {"SUCCEEDED", "REJECTED", "AWAITING_APPROVAL"}:
                        existing = operations.decode(op["result"])
                        run_payload.update(
                            text=existing["message"],
                            outcome=existing["outcome"],
                            pending_approval=existing["outcome"] == "AWAITING_APPROVAL",
                        )
                        return
                await conn.execute(
                    """INSERT INTO hitl_requests
                       (workflow_run_id, request_id, checkpoint_id, user_email, kind, payload, status)
                       VALUES ($1, $2, $3, $4, 'return_approval', $5::jsonb, 'pending')""",
                    usage_log_id,
                    run_payload["request_id"],
                    checkpoint_id,
                    user_email,
                    json.dumps({"text": run_payload.get("text", ""), "operation_id": op_id}, default=str),
                )
                if op is not None:
                    await operations.save_result(
                        conn,
                        op,
                        {
                            "success": False,
                            "outcome": "AWAITING_APPROVAL",
                            "status": "pending_approval",
                            "policy_version": POLICY_VERSION,
                            "workflow_run_id": str(usage_log_id),
                            "message": (
                                f"Return request is awaiting workflow approval. Resume the original run {usage_log_id}."
                            ),
                        },
                    )


@router.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(operation_scope)])
async def chat(
    body: ChatRequest,
    user: dict[str, Any] = Depends(optional_auth),
    _rate_limit: None = Depends(rate_limit_chat),
) -> ChatResponse:
    """主聊天端点 —— 把消息发送给编排器智能体。

    匿名（店面）调用方只能使用商品发现：不会创建或持久化任何会话，也不加载
    用户上下文。需要账号的工具会优雅降级（智能体会请用户登录）。
    """
    pool = get_pool()
    user_email = user.get("sub", "")
    user_id = user.get("user_id", "")
    is_anon = user.get("anonymous", False)

    conversation_id = body.conversation_id
    history: list[dict[str, str]] = []

    if not is_anon:
        # 解析或创建会话
        if conversation_id:
            conv = await pool.fetchrow(
                """SELECT id FROM conversations
                   WHERE id = $1 AND user_id = $2 AND is_active = TRUE""",
                conversation_id,
                user_id,
            )
            if not conv:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            title = body.message[:100] if len(body.message) > 100 else body.message
            row = await pool.fetchrow(
                """INSERT INTO conversations (user_id, title)
                   VALUES ($1, $2)
                   RETURNING id""",
                user_id,
                title,
            )
            conversation_id = str(row["id"])

        # 在保存本轮的用户消息*之前*加载对话历史 ——
        # 历史就是它之前发生的一切。_run_agent_native /
        # RunContext 会单独追加当前消息（见
        # shared/agent_host.py::_history_as_maf_messages）；在插入之后读取
        # 会让它在发给 LLM 的上下文中出现两次。
        history = await get_history_as_dicts(get_history_provider(pool=pool), conversation_id)

        # 保存用户消息
        await pool.execute(
            """INSERT INTO messages (conversation_id, role, content, agent_name)
               VALUES ($1, 'user', $2, NULL)""",
            conversation_id,
            body.message,
        )

        # 获取用户上下文（资料 + 近期订单）供智能体使用 —— 通过
        # ECommerceContextProvider 链注入（shared/context_providers.py）。
        user_row = await pool.fetchrow(
            "SELECT name, role, loyalty_tier, total_spend FROM users WHERE email = $1",
            user_email,
        )
        if user_row:
            recent_orders = await pool.fetch(
                """SELECT o.id, o.status, o.total, o.created_at
                   FROM orders o JOIN users u ON o.user_id = u.id
                   WHERE u.email = $1 ORDER BY o.created_at DESC LIMIT 5""",
                user_email,
            )
            _ = recent_orders  # 上下文通过 ContextProvider 注入

    _bind_session_to_conversation(None if is_anon else conversation_id)
    from shared.config import settings as run_settings
    from shared.paid_transport import current_root_run, current_run_deadline

    current_root_run.set(str(uuid4()))
    current_run_deadline.set(time.time() + run_settings.MAF_STREAM_TIMEOUT_SECONDS)

    from orchestrator.modes import RunContext, UnknownModeError, get_mode
    from shared.config import settings
    from shared.telemetry import agent_run_span

    mode_name = body.mode or settings.ORCHESTRATION_MODE
    try:
        mode = get_mode(mode_name)
    except UnknownModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    agents_involved: list[str] = ["orchestrator"]
    steps: list[dict[str, Any]] = []
    run_payload: dict[str, Any] = {}
    ctx = RunContext(history=history, conversation_id=conversation_id)

    with UsageTimer() as timer:
        with agent_run_span("orchestrator"):
            try:
                response_text = ""
                async for event in mode.run(body.message, ctx):
                    if event.kind == "run_completed":
                        run_payload = event.payload
                        response_text = run_payload.get("text", "")
                        agents_involved = run_payload.get("agents_involved", agents_involved)
                        steps = run_payload.get("steps", steps)
            except Exception:
                logger.exception(
                    "chat.agent_error user=%s conversation=%s mode=%s", user_email, conversation_id, mode_name
                )
                run_payload["outcome"] = "FAILED"
                response_text = "I apologize, but I encountered an issue processing your request. Please try again."

    if not is_anon:
        # 保存助手消息 + 更新会话 + 用量记录（仅已认证用户）
        assistant_message_id = await pool.fetchval(
            """INSERT INTO messages (conversation_id, role, content, agent_name, agents_involved)
               VALUES ($1, 'assistant', $2, 'orchestrator', $3) RETURNING id""",
            conversation_id,
            response_text,
            agents_involved,
        )
        await pool.execute(
            "UPDATE conversations SET last_message_at = NOW() WHERE id = $1",
            conversation_id,
        )
        usage = run_payload.get("usage") or {}
        usage_log_id = await log_agent_usage(
            user_id=user_id,
            agent_name="orchestrator",
            session_id=conversation_id,
            input_summary=body.message,
            duration_ms=timer.duration_ms,
            tool_calls_count=len(steps) if steps else max(len(agents_involved) - 1, 0),
            tokens_in=usage.get("input_token_count") or 0,
            tokens_out=usage.get("output_token_count") or 0,
        )
        if usage_log_id:
            for index, step in enumerate(steps):
                await log_execution_step(
                    usage_log_id=usage_log_id,
                    step_index=index,
                    tool_name=f"{step.get('agent', 'orchestrator')}:{step.get('tool_name', 'tool')}",
                    tool_input={"value": step.get("tool_input")},
                    tool_output={"value": step.get("tool_output"), "result_confirmed": step.get("result_confirmed")},
                    status=step.get("status", "success"),
                    duration_ms=step.get("duration_ms", 0),
                )
        await _link_run_artifacts(pool, usage_log_id, user_email, run_payload)
        if run_payload.get("decision", {}).get("context_enabled"):
            from shared.context_pipeline import remember_evidence

            await remember_evidence(conversation_id, steps)
        if run_payload.get("text") and run_payload["text"] != response_text:
            response_text = run_payload["text"]
            await pool.execute("UPDATE messages SET content = $2 WHERE id = $1", assistant_message_id, response_text)

    return ChatResponse(
        response=response_text,
        conversation_id=conversation_id or "",
        agents_involved=agents_involved,
        grounding=run_payload.get("grounding"),
        decision=run_payload.get("decision"),
        outcome=run_payload.get("outcome"),
    )


@router.post("/api/chat/stream", dependencies=[Depends(operation_scope)])
async def chat_stream(
    body: ChatRequest,
    request: Request,
    user: dict[str, Any] = Depends(optional_auth),
    _rate_limit: None = Depends(rate_limit_chat),
):
    """流式聊天端点 —— 在智能体生成令牌时发送 SSE 事件。

    匿名（店面）调用方只能使用商品发现：不持久化任何东西，也不加载用户
    上下文。
    """
    from orchestrator.modes import RunContext, UnknownModeError, get_mode
    from shared.config import settings
    from shared.context import current_stream_queue

    mode_name = body.mode or settings.ORCHESTRATION_MODE
    try:
        mode = get_mode(mode_name)
    except UnknownModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    pool = get_pool()
    user_email = user.get("sub", "")
    user_id = user.get("user_id", "")
    is_anon = user.get("anonymous", False)

    conversation_id = body.conversation_id
    history: list[dict[str, str]] = []

    if not is_anon:
        # 解析或创建会话
        if conversation_id:
            conv = await pool.fetchrow(
                """SELECT id FROM conversations
                   WHERE id = $1 AND user_id = $2 AND is_active = TRUE""",
                conversation_id,
                user_id,
            )
            if not conv:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            title = body.message[:100] if len(body.message) > 100 else body.message
            row = await pool.fetchrow(
                """INSERT INTO conversations (user_id, title)
                   VALUES ($1, $2)
                   RETURNING id""",
                user_id,
                title,
            )
            conversation_id = str(row["id"])

        # 在保存本轮的用户消息之前加载历史 —— 顺序为何重要见 chat() 中对应的
        # 注释。
        history = await get_history_as_dicts(get_history_provider(pool=pool), conversation_id)

        # 保存用户消息
        await pool.execute(
            """INSERT INTO messages (conversation_id, role, content, agent_name)
               VALUES ($1, 'user', $2, NULL)""",
            conversation_id,
            body.message,
        )

        # 获取用户上下文 —— 通过 ECommerceContextProvider 链注入。
        user_row = await pool.fetchrow(
            "SELECT name, role, loyalty_tier, total_spend FROM users WHERE email = $1",
            user_email,
        )
        if user_row:
            recent_orders = await pool.fetch(
                """SELECT o.id, o.status, o.total, o.created_at
                   FROM orders o JOIN users u ON o.user_id = u.id
                   WHERE u.email = $1 ORDER BY o.created_at DESC LIMIT 5""",
                user_email,
            )
            _ = recent_orders

    agents_involved: list[str] = ["orchestrator"]
    _bind_session_to_conversation(None if is_anon else conversation_id)
    from shared.config import settings as run_settings
    from shared.paid_transport import current_root_run, current_run_deadline

    current_root_run.set(str(uuid4()))
    current_run_deadline.set(time.time() + run_settings.MAF_STREAM_TIMEOUT_SECONDS)

    ctx = RunContext(history=history, conversation_id=conversation_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        """从流式智能体响应中产出 SSE 格式的事件。

        架构：编排器 LLM 的分块与专业智能体的响应分块都经由同一个
        asyncio.Queue 流动，因此浏览器能实时看到两层的令牌 —— 调用专业
        智能体期间没有静默空白。

        队列条目的形状：
          ("text",  chunk)                     — 显示文本（LLM 令牌、专业
                                                   智能体分块，或非 ``tool``
                                                   模式提取出的增量文本）
          ("delta", agent_name, chunk)          — 专业智能体流式令牌
                                                   （仅 ``tool`` 模式）
          ("frame", event_name, payload_dict)   — 非 ``tool`` 模式的
                                                   graph/handoff/checkpoint/
                                                   request_info/error 事件的
                                                   结构化 SSE 帧
          None                                  — 哨兵：运行结束
        """
        from shared.telemetry import agent_run_span

        full_response: list[str] = []
        full_bytes = 0
        truncated = False
        start_time = time.monotonic()
        deadline = start_time + float(settings.MAF_STREAM_TIMEOUT_SECONDS)
        max_bytes = int(settings.MAF_STREAM_MAX_BYTES)

        # 共享队列：编排器 LLM 分块 + 专业智能体流式分块。
        from shared.stream_queue import BoundedStreamQueue

        queue: _asyncio.Queue = BoundedStreamQueue(settings.MAF_STREAM_QUEUE_SIZE, settings.MAF_STREAM_QUEUE_BYTES)
        current_stream_queue.set(queue)

        # run_completed 完整载荷的可变容器（检查点/人工参与关联需要的不止
        # agents_involved）—— 对 "tool" 模式保持为空，它没有可读取的
        # OrchestrationEvent 流。
        run_payload_box: dict[str, Any] = {}

        if mode.name == "tool":
            from orchestrator.agent import create_orchestrator_agent
            from shared.agent_host import _run_agent_native_stream

            agent = create_orchestrator_agent()
            run_metadata: dict[str, Any] = {}

            async def _run_mode_task() -> None:
                reset_steps()
                reset_grounding_ledger()
                with agent_run_span("orchestrator"):
                    try:
                        async for chunk in _run_agent_native_stream(
                            agent,
                            body.message,
                            history=history,
                            metadata_box=run_metadata,
                        ):
                            await queue.put(("text", chunk))
                        if "grounding" in run_metadata:
                            await queue.put(("frame", "grounding", run_metadata["grounding"]))
                    except Exception:
                        logger.exception(
                            "chat_stream.agent_error user=%s conversation=%s",
                            user_email,
                            conversation_id,
                        )
                        error_msg = "I apologize, but I encountered an issue. Please try again."
                        await queue.put(("text", error_msg))
                await queue.put(None)  # 哨兵
        else:
            from orchestrator.events import delta_text

            async def _run_mode_task() -> None:
                final_agents_involved = list(agents_involved)
                # 并非每个模式都会流式输出令牌级增量 —— MAF 只会为
                # ctx.yield_output() 调用发出 "output" WorkflowEvent（已对照
                # 一次真实的购前调研运行验证：扇出/扇入执行器用的是
                # ctx.send_message()，那完全不会产生 delta，而那唯一一次
                # yield_output() 携带的是原始状态 dataclass，而非
                # delta_text() 能读取的 AgentResponseUpdate 形状内容）。
                # 跟踪是否真有内容抵达显示层，以便 run_completed 能退而做一次
                # 运行结束时的整块倾倒 —— 这正是 "tool" 模式免费获得的保证。
                streamed_any_text = False
                with agent_run_span("orchestrator"):
                    try:
                        async for event in mode.run(body.message, ctx):
                            if event.kind == "delta":
                                text = delta_text(event.payload)
                                if text:
                                    streamed_any_text = True
                                    await queue.put(("text", text))
                            elif event.kind in ("node_enter", "node_exit"):
                                await queue.put(
                                    (
                                        "frame",
                                        "node",
                                        {
                                            "node_id": event.node_id,
                                            "phase": "enter" if event.kind == "node_enter" else "exit",
                                            "agent": event.agent,
                                            "payload": event.payload,
                                        },
                                    )
                                )
                            elif event.kind == "handoff":
                                await queue.put(("frame", "handoff", {"node_id": event.node_id, **event.payload}))
                            elif event.kind == "tool_call":
                                await queue.put(
                                    (
                                        "frame",
                                        "step",
                                        {
                                            "agent": event.agent,
                                            "tool_name": event.node_id,
                                            **event.payload,
                                        },
                                    )
                                )
                            elif event.kind == "checkpoint":
                                await queue.put(("frame", "checkpoint", {"node_id": event.node_id, **event.payload}))
                            elif event.kind == "request_info":
                                await queue.put(("frame", "request_info", {"node_id": event.node_id, **event.payload}))
                            elif event.kind == "error":
                                await queue.put(("frame", "error", {"node_id": event.node_id, **event.payload}))
                                await queue.put(("text", " [an error occurred] "))
                            elif event.kind == "run_completed":
                                run_payload_box.update(event.payload)
                                if event.payload.get("decision"):
                                    await queue.put(("frame", "decision", event.payload["decision"]))
                                final_agents_involved = event.payload.get("agents_involved", final_agents_involved)
                                if not streamed_any_text:
                                    final_text = event.payload.get("text", "")
                                    if final_text:
                                        await queue.put(("text", final_text))
                            # run_started / graph / grounding：暂无可渲染的内容。
                    except Exception:
                        logger.exception(
                            "chat_stream.mode_error user=%s conversation=%s mode=%s",
                            user_email,
                            conversation_id,
                            mode_name,
                        )
                        await queue.put(("text", "I apologize, but I encountered an issue. Please try again."))
                agents_involved[:] = final_agents_involved
                await queue.put(None)  # 哨兵

        agent_task = _asyncio.create_task(_run_mode_task())

        try:
            try:
                while True:
                    if await request.is_disconnected():
                        logger.info(
                            "chat_stream.client_disconnected conversation=%s elapsed_ms=%d",
                            conversation_id,
                            int((time.monotonic() - start_time) * 1000),
                        )
                        agent_task.cancel()
                        break

                    if time.monotonic() > deadline:
                        logger.warning(
                            "chat_stream.timeout conversation=%s budget_s=%s",
                            conversation_id,
                            settings.MAF_STREAM_TIMEOUT_SECONDS,
                        )
                        timeout_msg = " [stream timed out — please retry]"
                        full_response.append(timeout_msg)
                        yield f"data: {timeout_msg}\n\n"
                        agent_task.cancel()
                        break

                    try:
                        item = await _asyncio.wait_for(queue.get(), timeout=0.1)
                    except TimeoutError:
                        continue

                    if item is None:
                        break

                    if item[0] == "text":
                        chunk: str = item[1]
                        if not truncated:
                            chunk_bytes = len(chunk.encode("utf-8"))
                            if full_bytes + chunk_bytes > max_bytes:
                                truncated = True
                                marker = " [response truncated at limit]"
                                full_response.append(marker)
                                yield f"data: {marker}\n\n"
                            else:
                                full_bytes += chunk_bytes
                                full_response.append(chunk)
                                yield encode_sse(chunk)

                    elif item[0] == "delta":
                        # 专业智能体令牌 —— 立即作为实时预览转发给浏览器。
                        # 这些出现在「工具调用间隙」期间，因此用户看到的是
                        # 连续流动而非静默。刻意不追加到 full_response
                        # （Phase 8.1）：编排器自己的 "text" 流在
                        # call_specialist_agent 的工具调用解析后产生，会重述
                        # 同样的内容 —— orchestrator.yaml 明确指示它在自己的
                        # 框定文字之上逐字重新发出专业智能体的卡片围栏 ——
                        # 因此把两者都持久化会让每个工具模式的答案重复。
                        # 客户端与此对称：一旦 "text" 开始到达，它就替换而非
                        # 追加可见预览（见 web/src/lib/api.ts 的 onDeltaChunk）。
                        _agent_src: str = item[1]
                        delta_chunk: str = item[2]
                        yield encode_sse(delta_chunk, "delta")

                    elif item[0] == "frame":
                        # 来自非 "tool" 模式的结构化事件（node/handoff/
                        # checkpoint/request_info/error）—— 不是显示文本，
                        # 因此绕过上面的字节上限/截断记账。
                        frame_name: str = item[1]
                        frame_payload: dict[str, Any] = item[2]
                        yield f"event: {frame_name}\ndata: {json.dumps(frame_payload, default=str)}\n\n"

            finally:
                if not agent_task.done():
                    agent_task.cancel()
                try:
                    await agent_task
                except _asyncio.CancelledError:
                    pass
        except _asyncio.CancelledError:
            # Starlette 自己的 ASGI 层断开处理会直接取消本生成器的任务 ——
            # 它与上面的 request.is_disconnected() 轮询形成竞争，而它可能赢，
            # 直接越过循环（以及循环自己的 finally）而不走到下面的正常完成
            # 路径。此时通过游离任务（不受这同一次取消影响）持久化迄今累积的
            # 文本，而不是静默丢弃 —— 见 #10。
            if not is_anon:
                cancelled_steps = get_steps() if mode.name == "tool" else []
                for s in cancelled_steps:
                    s.setdefault("agent", "orchestrator")
                _spawn_persist_task(
                    _persist_assistant_turn(
                        pool=pool,
                        conversation_id=conversation_id,
                        response_text="".join(full_response),
                        agents_involved=list(agents_involved),
                        steps=cancelled_steps,
                        user_id=user_id,
                        user_email=user_email,
                        body_message=body.message,
                        start_time=start_time,
                        run_payload_box=run_payload_box,
                        stream_usage=(run_metadata.get("_maf_usage") or {}) if mode.name == "tool" else {},
                        disconnected=True,
                    )
                )
            raise

        response_text = "".join(full_response)

        if mode.name == "tool":
            # 排空捕获到的工具步骤 → 时间线帧 + agents_involved。
            steps = get_steps()
            for s in steps:
                s.setdefault("agent", "orchestrator")
            agents_involved[:] = list(dict.fromkeys(["orchestrator", *[s.get("agent", "orchestrator") for s in steps]]))
            for s in steps:
                # 某个专业智能体的步骤已经由 orchestrator/agent.py 在工具返回时
                # 实时发出过了。在这里再发一次会让时间线中每一行都重复。
                # 弹出该标记也能让它不进持久化元数据 —— 传输层标志在那里
                # 毫无道理。
                if s.pop("_live", False):
                    continue
                yield f"event: step\ndata: {json.dumps(s, default=str)}\n\n"
            stream_usage = run_metadata.get("_maf_usage") or {}
        else:
            # 非 "tool" 模式已经在其 "step"/"node"/"handoff" 帧发生时就地发出了
            # 它们；agents_involved 已由该模式的 run_completed 事件设置。
            steps = []
            stream_usage = {}

        metadata = json.dumps({"conversation_id": conversation_id, "agents_involved": agents_involved})
        yield f"event: metadata\ndata: {metadata}\n\n"

        # 持久化助手消息 + 时间线 —— 仅已认证用户（匿名店面聊天没有可写入的
        # 会话）。
        #
        # 它仍然是游离任务（见 _spawn_persist_task），这样与正常完成最末尾
        # 竞争的断开操作无法把它丢掉 —— 但它会在 [DONE] 之前被 *await*，
        # 而不是在它之后发射（#9）。[DONE] 是客户端判断本轮结束的提示，
        # 输入框会据此重新启用；否则紧接着发出的追问会在这次 INSERT 落库
        # 之前读取历史，从而丢掉它所追问的那一轮。await 一个被 shield 的
        # 游离任务能同时保住两个性质：在告知客户端可以继续之前该行已持久化，
        # 而这里的取消仍然无法杀掉这次写入。
        if is_anon:
            yield "data: [DONE]\n\n"
            return
        persist_task = _spawn_persist_task(
            _persist_assistant_turn(
                pool=pool,
                conversation_id=conversation_id,
                response_text=response_text,
                agents_involved=list(agents_involved),
                steps=steps,
                user_id=user_id,
                user_email=user_email,
                body_message=body.message,
                start_time=start_time,
                run_payload_box=run_payload_box,
                stream_usage=stream_usage,
                disconnected=False,
            )
        )
        try:
            await _asyncio.shield(persist_task)
        except _asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("chat.persist_failed conversation=%s", conversation_id)

        # 现在这次运行有了 id，可以给它命名了。`pending_approval` 让聊天线程
        # 能渲染自己的审批控件，而不必把用户送到 /runs 去找他们刚触发的暂停
        # —— 并且刻意在持久化之后读取，因为一次未能写入其 hitl_requests 行的
        # 暂停，是任何 UI 都无法据以行动的。
        run_id = run_payload_box.get("usage_log_id")
        if run_id:
            yield (
                "event: run\ndata: "
                + json.dumps(
                    {
                        "run_id": str(run_id),
                        "pending_approval": bool(run_payload_box.get("pending_approval")),
                    },
                    default=str,
                )
                + "\n\n"
            )

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
