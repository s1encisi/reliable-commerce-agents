"""编排内省路由 —— 模式清单、图、对比、恢复。

``GET /modes`` 与 ``GET /modes/{name}/graph`` 读取真实的模式注册表
（``orchestrator/modes/``）—— Phase 1.2 把五个模式接入了它；这条路由只需
去问，而不必硬编码一份会与 ``/api/chat`` 实际能运行的东西逐渐脱节的列表
（一份过时的硬编码列表，正是本项目其余部分存在所要修复的那类文档/代码
鸿沟）。``POST /compare`` 自 Phase 1.6c 起是真实的：它让一个提示词依次
穿过多个模式（顺序执行 —— 公平的延迟对比且无资源争用，胜过更快但更浑浊的
并发运行），并返回每个模式的文本/延迟/步骤/图 —— 这个产物就是用来截图展示
同一提示词下工具 vs. 工作流 vs. 处理权交接并排对比的。计划原始草图中的
``tokens``/``est_cost_usd``/``grounding`` 刻意还没有出现在响应里 —— 它们
需要 Phase 3.5 的 ``shared/cost.py`` 与 Phase 2 的事实核验器，两者都尚不存在；
返回编造的数字会比不返回更糟。``POST /{run_id}/resume`` 自 Phase 1.5 起是
真实的：它从 Phase 1.5 的 ``chat.py`` 关联逻辑所写入的 ``hitl_requests`` 行，
恢复一次暂停的 ``workflow:return-replace`` 运行。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.context import current_user_email, current_user_role
from shared.db import get_pool

from .legacy import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orchestration")

MAX_COMPARE_MODES = 5


@router.get("/modes")
async def list_modes() -> list[dict[str, object]]:
    """``/api/chat`` 可以被要求运行的每个模式，以及各自支持什么。"""
    from orchestrator.modes import list_modes as registry_list_modes

    return registry_list_modes()


@router.get("/modes/{name}/graph")
async def get_mode_graph(name: str) -> dict[str, object]:
    """某个模式的静态 Mermaid 图；对于按轮路由而非沿固定拓扑的模式
    （``tool``、``handoff``）则为 ``None`` —— 见各模式自己的
    ``graph_mermaid()``。"""
    from orchestrator.modes import UnknownModeError, get_mode

    try:
        mode = get_mode(name)
    except UnknownModeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"name": name, "mermaid": mode.graph_mermaid()}


class CompareRequest(BaseModel):
    message: str
    modes: list[str]


class CompareModeResult(BaseModel):
    mode: str
    label: str
    text: str
    latency_ms: int
    agents_involved: list[str]
    step_count: int
    graph_mermaid: str | None
    error: str | None = None


class CompareResponse(BaseModel):
    message: str
    results: list[CompareModeResult]


@router.post("/compare", response_model=CompareResponse)
async def compare_modes(body: CompareRequest, user: dict[str, Any] = Depends(require_auth)) -> CompareResponse:
    """让一个提示词依次穿过多个模式，并报告各模式的结果。

    独立运行 —— 无会话、无持久化历史（每个模式都用
    ``RunContext(history=[])``）—— 这是在孤立场景下用一个提示词对比各模式，
    而不是进行中聊天里的一轮。顺序执行：各模式共享同一个 Postgres 连接池与
    专业智能体服务，并发运行会争用相同资源并产生更浑浊的延迟数字，而不会
    更快或更有意义。某个模式抛错不会中止整个对比 —— 它会在自己的 ``error``
    字段中报告，这样一个坏掉的模式不会掩盖其他模式的结果。
    """
    from orchestrator.modes import UnknownModeError, get_mode
    from orchestrator.modes import list_modes as registry_list_modes
    from orchestrator.modes.base import RunContext

    if not body.modes:
        raise HTTPException(status_code=400, detail="modes must be a non-empty list")
    if len(body.modes) > MAX_COMPARE_MODES:
        raise HTTPException(status_code=400, detail=f"modes must have at most {MAX_COMPARE_MODES} entries")

    labels_by_name = {m["name"]: m["label"] for m in registry_list_modes()}
    results: list[CompareModeResult] = []

    for name in body.modes:
        try:
            mode = get_mode(name)
        except UnknownModeError as exc:
            results.append(
                CompareModeResult(
                    mode=name,
                    label=name,
                    text="",
                    latency_ms=0,
                    agents_involved=[],
                    step_count=0,
                    graph_mermaid=None,
                    error=str(exc),
                )
            )
            continue

        ctx = RunContext(history=[])
        start = time.monotonic()
        text = ""
        agents_involved: list[str] = []
        tool_call_count = 0
        node_enter_count = 0
        error: str | None = None
        try:
            async for event in mode.run(body.message, ctx):
                if event.kind == "tool_call":
                    tool_call_count += 1
                elif event.kind == "node_enter":
                    node_enter_count += 1
                elif event.kind == "run_completed":
                    text = event.payload.get("text", "")
                    agents_involved = event.payload.get("agents_involved", [])
        except Exception as exc:
            logger.exception("compare.mode_error mode=%s", name)
            error = str(exc)
        latency_ms = int((time.monotonic() - start) * 1000)

        results.append(
            CompareModeResult(
                mode=name,
                label=labels_by_name.get(name, name),
                text=text,
                latency_ms=latency_ms,
                agents_involved=agents_involved,
                # tool_call 事件只在 "tool" 模式下触发（adapt_step()）；
                # node_enter 是工作流图模式对应的「发生了多少事」信号 ——
                # 见 orchestrator/events.py。
                step_count=tool_call_count or node_enter_count,
                graph_mermaid=mode.graph_mermaid(),
                error=error,
            )
        )

    return CompareResponse(message=body.message, results=results)


class ResumeRequest(BaseModel):
    approved: bool


@router.post("/{run_id}/resume")
async def resume_run(run_id: str, body: ResumeRequest, user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    from shared.after_sales.locks import workflow_resume_lock

    async with workflow_resume_lock(run_id) as acquired:
        if not acquired:
            raise HTTPException(404, "This workflow is already being resumed")
        return await _resume_run_locked(run_id, body, user)


async def _resume_run_locked(
    run_id: str, body: ResumeRequest, user: dict[str, Any] = Depends(require_auth)
) -> dict[str, Any]:
    """从已提交的检查点状态恢复一个因工作流内人工参与而暂停的工作流。

    为 ``run_id`` 查找最近一条*待处理*的 ``hitl_requests`` 行（除非是管理员，
    否则限定为调用者 —— 与 ``GET /api/runs/{id}/checkpoints`` 使用的是同一套
    归属校验），经由 ``ReturnReplaceMode.resume()`` 恢复（目前这是唯一有东西
    可恢复的模式），并把该请求标记为已解决。没有 ``request_id``/``checkpoint_id``
    的请求早于 Phase 1.5 的检查点接线，无法以这种方式恢复 —— 会以 409 暴露
    出来，而不是被静默地当作「未找到」。
    """
    pool = get_pool()
    email = current_user_email.get()
    role = current_user_role.get()

    if role == "admin":
        hitl = await pool.fetchrow(
            """SELECT * FROM hitl_requests
               WHERE workflow_run_id = $1 AND status IN ('pending', 'processing')
               ORDER BY created_at DESC LIMIT 1""",
            run_id,
        )
    else:
        hitl = await pool.fetchrow(
            """SELECT * FROM hitl_requests
               WHERE workflow_run_id = $1 AND status IN ('pending', 'processing') AND user_email = $2
               ORDER BY created_at DESC LIMIT 1""",
            run_id,
            email,
        )
    if not hitl:
        raise HTTPException(status_code=404, detail="No pending approval found for this run")
    if hitl["kind"] != "return_approval":
        raise HTTPException(status_code=400, detail=f"Resume not supported for request kind {hitl['kind']!r}")
    if not hitl["request_id"] or not hitl["checkpoint_id"]:
        raise HTTPException(status_code=409, detail="This pending request predates checkpoint-based resume")

    if hitl["status"] == "processing":
        recorded = json.loads(hitl["response"]) if isinstance(hitl["response"], str) else hitl["response"]
        if not recorded or recorded.get("approved") != body.approved:
            raise HTTPException(409, "Recovery must preserve the recorded approval decision")
    else:
        claimed = await pool.fetchval(
            """UPDATE hitl_requests SET status = 'processing', response = $2::jsonb
               WHERE id = $1 AND status = 'pending' RETURNING id""",
            hitl["id"],
            json.dumps({"approved": body.approved}),
        )
        if claimed is None:
            raise HTTPException(status_code=404, detail="No pending approval found for this run")

    from orchestrator.modes import get_mode

    mode = get_mode("workflow:return-replace")
    final_payload: dict[str, Any] = {}
    owner_role = await pool.fetchval("SELECT role FROM users WHERE email = $1", hitl["user_email"])
    owner_token = current_user_email.set(hitl["user_email"])
    role_token = current_user_role.set(owner_role or "customer")
    try:
        async for event in mode.resume(
            checkpoint_id=str(hitl["checkpoint_id"]), request_id=hitl["request_id"], approved=body.approved
        ):
            if event.kind == "run_completed":
                final_payload = event.payload
    finally:
        current_user_email.reset(owner_token)
        current_user_role.reset(role_token)

    await pool.execute(
        """UPDATE hitl_requests
           SET status = $1, responded_at = NOW(), response = $2::jsonb
           WHERE id = $3""",
        "approved" if body.approved else "rejected",
        json.dumps(
            {
                "approved": body.approved,
                "outcome": final_payload.get("outcome"),
                "operation_id": final_payload.get("operation_id"),
            }
        ),
        hitl["id"],
    )
    new_checkpoint_id = final_payload.get("latest_checkpoint_id")
    if new_checkpoint_id:
        await pool.execute(
            "UPDATE workflow_checkpoints SET usage_log_id = $1 WHERE checkpoint_id = $2",
            run_id,
            new_checkpoint_id,
        )

    return {
        "run_id": run_id,
        "approved": body.approved,
        "outcome": final_payload.get("outcome"),
        "operation_id": final_payload.get("operation_id"),
        "text": final_payload.get("text", ""),
        "agents_involved": final_payload.get("agents_involved", []),
    }
