"""采购控制面：需求确认后排队运行；SSE只读取事件，不推进业务。"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from orchestrator.routes.legacy import require_auth
from shared.campaign_budget import BudgetError
from shared.config import settings
from shared.context import current_user_email
from shared.db import get_pool
from shared.procurement import state
from shared.procurement.model_io import parse_task, require_model_authorization
from shared.procurement.models import ProcurementSpec

router = APIRouter()


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)


class ParseTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=6000)


class ConfirmSpec(Revision):
    requirements: ProcurementSpec


def ensure_enabled(email: str, *, mode: str | None = None) -> None:
    if not settings.PROCUREMENT_ENABLED:
        raise HTTPException(403, "采购运行时未启用")
    allowlist = {value.strip() for value in settings.PROCUREMENT_ALLOWLIST.split(",") if value.strip()}
    if allowlist and email not in allowlist:
        raise HTTPException(403, "当前账号尚未启用采购实验")
    if settings.MCP_ENABLED:
        raise HTTPException(409, "采购能力尚未验证当前MCP配置，请使用明确的原生工具展示配置")
    if (mode or settings.PROCUREMENT_MODE) == "model":
        try:
            require_model_authorization()
        except BudgetError as exc:
            raise HTTPException(403, str(exc)) from exc


async def state_call(fn, *args, **kwargs):
    try:
        return await fn(*args, **kwargs)
    except state.TaskNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except state.TaskConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except state.BudgetExceeded as exc:
        raise HTTPException(429, str(exc)) from exc


async def owned(task_id: UUID):
    email = current_user_email.get("")
    task = await state.get_task(get_pool(), task_id, email)
    if task is None:
        raise HTTPException(404, "采购任务不存在")
    return task


@router.post("/api/tasks/parse", status_code=201)
async def parse_procurement(body: ParseTask, user: dict = Depends(require_auth)) -> dict:
    email = current_user_email.get("")
    ensure_enabled(email)
    task = await state_call(state.create_task, get_pool(), email, body.goal, execution_mode=settings.PROCUREMENT_MODE)
    try:
        proposal = await parse_task(task)
        draft = {
            **proposal.draft_spec,
            "_parse": {
                "source": proposal.source,
                "questions": proposal.questions,
                "unsupported_conditions": proposal.unsupported_conditions,
            },
        }
        reason = "；".join(proposal.questions) or "请核对并确认结构化需求；当前尚未执行采购任务。"
        return await state_call(
            state.update_draft,
            get_pool(),
            task["id"],
            email,
            task["revision"],
            draft,
            status="needs_input" if proposal.questions else "draft",
            stop_reason=reason,
        )
    except (BudgetError, ValueError) as exc:
        return await state_call(
            state.update_draft,
            get_pool(),
            task["id"],
            email,
            task["revision"],
            {},
            status="needs_input",
            stop_reason=f"解析未完成，请手动核对需求：{type(exc).__name__}",
        )


@router.post("/api/tasks/{task_id}/confirm-spec")
async def confirm_procurement(task_id: UUID, body: ConfirmSpec, user: dict = Depends(require_auth)) -> dict:
    task = await owned(task_id)
    ensure_enabled(task["owner_email"], mode=task["execution_mode"])
    if (task.get("draft_spec") or {}).get("_parse", {}).get("unsupported_conditions"):
        raise HTTPException(422, "原需求含当前不能核实的硬条件，请修改原始需求并重新解析")
    return await state_call(
        state.confirm_spec, get_pool(), task_id, task["owner_email"], body.revision, body.requirements
    )


@router.post("/api/tasks/{task_id}/start", status_code=202)
async def start_procurement(task_id: UUID, body: Revision, user: dict = Depends(require_auth)) -> dict:
    task = await owned(task_id)
    ensure_enabled(task["owner_email"], mode=task["execution_mode"])
    return await state_call(state.queue_task, get_pool(), task_id, task["owner_email"], body.revision)


@router.post("/api/tasks/{task_id}/pause", status_code=202)
async def pause_procurement(task_id: UUID, body: Revision, user: dict = Depends(require_auth)) -> dict:
    task = await owned(task_id)
    return await state_call(state.request_pause, get_pool(), task_id, task["owner_email"], body.revision)


@router.get("/api/tasks/{task_id}/events")
async def procurement_events(
    task_id: UUID,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None),
    user: dict = Depends(require_auth),
):
    task = await owned(task_id)
    email = task["owner_email"]
    if last_event_id:
        try:
            after_seq = max(after_seq, int(last_event_id))
        except ValueError as exc:
            raise HTTPException(422, "事件序号必须是整数") from exc

    async def stream():
        cursor = after_seq
        while not await request.is_disconnected():
            events = await state.list_events(get_pool(), task_id, email, after_seq=cursor)
            for event in events:
                cursor = event["seq"]
                serialized = json.dumps(event, ensure_ascii=False, default=str)
                yield f"id: {cursor}\nevent: {event['event']}\ndata: {serialized}\n\n"
            latest = await state.get_task(get_pool(), task_id, email)
            if latest is None or latest["status"] in {
                "completed",
                "failed",
                "budget_exhausted",
                "needs_review",
                "paused",
                "draft",
                "needs_input",
            }:
                break
            if not events:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
