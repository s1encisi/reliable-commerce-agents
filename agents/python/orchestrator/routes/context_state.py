"""会话所有者显式编辑目标/约束；以数据库 revision 防止覆盖并发更新。"""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from orchestrator.routes.legacy import require_auth
from shared.config import settings
from shared.context import current_user_email
from shared.context_pipeline import ContextOverflowError, TaskContext, build_context, revise_context
from shared.db import get_pool

router = APIRouter()


class ContextEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    goal: str | None = Field(default=None, min_length=1, max_length=6000)
    constraints: list[str] | None = Field(default=None, max_length=30)


def payload(conversation_id: UUID, revision: int, context: TaskContext) -> dict:
    return {"conversation_id": str(conversation_id), "revision": revision, "context": context.state()}


@router.get("/api/conversations/{conversation_id}/context")
async def get_context(conversation_id: UUID, user: dict = Depends(require_auth)) -> dict:
    row = await get_pool().fetchrow(
        "SELECT s.snapshot,s.revision FROM conversations c JOIN users u ON u.id=c.user_id "
        "LEFT JOIN conversation_contexts s ON s.conversation_id=c.id WHERE c.id=$1 AND u.email=$2",
        conversation_id,
        current_user_email.get(""),
    )
    if row is None:
        raise HTTPException(404, "会话不存在")
    raw = row["snapshot"]
    context = TaskContext(**(json.loads(raw) if isinstance(raw, str) else raw)) if raw else TaskContext(goal="")
    return payload(conversation_id, row["revision"] or 0, context)


@router.patch("/api/conversations/{conversation_id}/context")
async def edit_context(conversation_id: UUID, body: ContextEdit, user: dict = Depends(require_auth)) -> dict:
    if body.goal is None and body.constraints is None:
        raise HTTPException(422, "请提供目标或约束修改")
    if body.constraints is not None and any(not text.strip() or len(text) > 2000 for text in body.constraints):
        raise HTTPException(422, "每项约束须为1至2000字符的非空文本")
    async with get_pool().acquire() as conn, conn.transaction():
        uid = await conn.fetchval(
            "SELECT c.user_id FROM conversations c JOIN users u ON u.id=c.user_id "
            "WHERE c.id=$1 AND u.email=$2 FOR UPDATE OF c",
            conversation_id,
            current_user_email.get(""),
        )
        if uid is None:
            raise HTTPException(404, "会话不存在")
        row = await conn.fetchrow(
            "SELECT snapshot,revision FROM conversation_contexts WHERE conversation_id=$1 FOR UPDATE",
            conversation_id,
        )
        revision = row["revision"] if row else 0
        if revision != body.revision:
            raise HTTPException(409, "上下文已更新，请重新读取后修改")
        raw = row["snapshot"] if row else None
        context = TaskContext(**(json.loads(raw) if isinstance(raw, str) else raw)) if raw else TaskContext(goal="")
        try:
            updated = revise_context(context, goal=body.goal, constraints=body.constraints)
            if not updated.goal and not updated.goal_needs_input:
                raise ValueError("首次编辑请填写目标")
            if updated.epoch != context.epoch:
                updated.history_since = (await conn.fetchval("SELECT clock_timestamp()")).isoformat()
            updated = build_context("", [], pinned=updated, max_bytes=settings.CONTEXT_MAX_BYTES)
        except (ValueError, ContextOverflowError) as exc:
            raise HTTPException(422, str(exc)) from exc
        await conn.execute(
            "INSERT INTO conversation_contexts(conversation_id,user_id,snapshot,revision) VALUES($1,$2,$3::jsonb,1) "
            "ON CONFLICT(conversation_id) DO UPDATE SET snapshot=EXCLUDED.snapshot, "
            "revision=conversation_contexts.revision+1,updated_at=NOW()",
            conversation_id,
            uid,
            json.dumps(updated.state(), ensure_ascii=False),
        )
        return payload(conversation_id, revision + 1, updated)
