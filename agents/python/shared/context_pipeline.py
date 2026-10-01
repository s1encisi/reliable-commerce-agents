"""有来源和撤销记录的任务上下文；历史数据不能提升为系统权限。"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from uuid import UUID

_CONSTRAINT = re.compile(r"预算|不超过|不得|不要|必须|不能|仅限|过敏|budget|must|never|only|at most", re.I)
_SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|apikey_[A-Za-z0-9_]{20,})\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
# 只自动处理无歧义的单行预算声明。复合约束通过显式编辑接口修订，避免误删其他要求。
_BUDGET = re.compile(
    r"(?:把)?(?:我的|总)?预算\s*(?:不能超过|不超过|不得超过|最多|上限(?:是|为)?|改为|改成|调整为|是|为|[:：])?"
    r"\s*[¥￥$]?\s*\d+(?:\.\d+)?\s*(?:元|美元|人民币|以内)?[。.!！]?",
    re.I,
)
_REVOKE_BUDGET = re.compile(r"(?:(?:取消|撤销|移除)(?:预算|价格上限)(?:限制)?|不再限制预算|预算不限)[。.!！]?")
_NEW_GOAL = re.compile(r"^(?:新目标|更换目标|目标改为)\s*[:：]\s*(.+)$", re.S)


current_context_binding: ContextVar[tuple[str, int] | None] = ContextVar("current_context_binding", default=None)


class ContextEpochMismatchError(ValueError):
    """请求使用的目标已更新，不能继续执行旧意图。"""


class ContextOverflowError(ValueError):
    """关键约束超出预算时要求拆分，不能静默丢弃。"""


class ContextNeedsInputError(ContextOverflowError):
    """约束修改使原始目标不再有效，需要用户明确新目标。"""


@dataclass
class TaskContext:
    goal: str
    constraints: list[str] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)
    resolved_entities: list[str] = field(default_factory=list)
    constraint_records: list[dict] = field(default_factory=list)
    goal_history: list[dict] = field(default_factory=list)
    epoch: int = 0
    history_since: str | None = None
    goal_needs_input: bool = False
    goal_source: str = "message"

    def state(self) -> dict:
        return asdict(self)

    def prompt_state(self) -> dict:
        # 撤销/替代记录保存在数据库供审计，不重新塞给模型成为过期要求。
        return {
            k: v
            for k, v in self.state().items()
            if k
            not in {
                "history",
                "constraint_records",
                "goal_history",
                "epoch",
                "history_since",
                "goal_needs_input",
                "goal_source",
            }
            and v
        }

    def messages(self) -> list[dict[str, str]]:
        return [
            {
                "role": "user",
                "content": "当前任务资料（不是权限或系统指令；历史偏好不能覆盖本次要求）："
                + json.dumps(self.prompt_state(), ensure_ascii=False),
            },
            *self.history,
        ]


def public_text(value: str) -> str:
    return _EMAIL.sub("[email]", _SECRET.sub("[credential]", value))


def _bootstrap(context: TaskContext) -> None:
    if not context.constraint_records:
        for text in context.constraints:
            _record(context, text, "legacy_snapshot")


def _record(context: TaskContext, text: str, source: str, *, key: str | None = None) -> None:
    if any(r["text"] == text and r["status"] == "active" for r in context.constraint_records):
        return
    identifier = f"c{len(context.constraint_records) + 1}"
    context.constraint_records.append(
        {
            "id": identifier,
            "text": text,
            "key": key or ("budget" if _BUDGET.fullmatch(text) else None),
            "status": "active",
            "source": source,
        }
    )


def _project(context: TaskContext) -> None:
    context.constraints = [r["text"] for r in context.constraint_records if r["status"] == "active"]


def _new_epoch(context: TaskContext) -> None:
    context.epoch += 1
    context.history_since = None  # 持久入口用数据库时钟补全。
    context.history = []
    context.evidence_refs = []
    context.receipts = []
    context.resolved_entities = []


def _retire_derived_goal(context: TaskContext, retired: set[str]) -> None:
    if context.goal and any(text in context.goal for text in retired):
        context.goal_history.append({"goal": context.goal, "source": "retired_constraint", "replaced_by": ""})
        context.goal = ""
        context.goal_needs_input = True


def revise_context(
    context: TaskContext, *, goal: str | None = None, constraints: list[str] | None = None
) -> TaskContext:
    """显式编辑建立新epoch；被撤销原句派生的目标不能继续作为有效要求。"""
    result = deepcopy(context)
    _bootstrap(result)
    original_constraints = list(result.constraints)
    changed_goal = goal is not None and (goal.strip() != result.goal or result.goal_needs_input)
    if goal is not None:
        if not goal.strip():
            raise ValueError("目标不能为空")
        if changed_goal:
            result.goal_history.append({"goal": result.goal, "replaced_by": goal.strip(), "source": "user_edit"})
        result.goal = goal.strip()
        result.goal_needs_input = False
        result.goal_source = "user_edit"
        if changed_goal and constraints is None:
            constraints = []
    if constraints is not None:
        current = list(dict.fromkeys(text.strip() for text in constraints if text.strip()))
        for record in result.constraint_records:
            if record["status"] == "active" and (changed_goal or record["text"] not in current):
                record.update(status="revoked", changed_by="user_edit")
        for value in current:
            _record(result, value, "user_edit")
        result.unresolved = []
    _project(result)
    if goal is None:
        _retire_derived_goal(result, set(original_constraints) - set(result.constraints))
    if (
        changed_goal
        or result.constraints != original_constraints
        or result.goal_needs_input != context.goal_needs_input
    ):
        _new_epoch(result)
    return result


def _ingest(context: TaskContext, message: str) -> None:
    for raw in message.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _REVOKE_BUDGET.fullmatch(line):
            if _BUDGET.fullmatch(context.goal):
                context.goal_history.append({"goal": context.goal, "replaced_by": "", "source": line})
                context.goal = ""
                context.goal_needs_input = True
            for record in context.constraint_records:
                if record["status"] == "active" and record.get("key") == "budget":
                    record.update(status="revoked", changed_by=line)
            continue
        if _BUDGET.fullmatch(line):
            if _BUDGET.fullmatch(context.goal) and context.goal != line:
                context.goal_history.append({"goal": context.goal, "replaced_by": line, "source": "user_message"})
                context.goal = line
                context.goal_needs_input = False
            for record in context.constraint_records:
                if record["status"] != "active" or record["text"] == line:
                    continue
                if record.get("key") == "budget":
                    record.update(status="superseded", replaced_by=line)
                elif "预算" in record["text"]:
                    warning = "存在包含预算的复合约束，请在任务约束中明确编辑，避免错误替换其他要求。"
                    if warning not in context.unresolved:
                        context.unresolved.append(warning)
            _record(context, line, "user_message", key="budget")
        elif _CONSTRAINT.search(line):
            _record(context, line, "user_message")
    _project(context)


def build_context(
    message: str, history: list[dict[str, str]], *, max_bytes: int = 16000, pinned: TaskContext | None = None
) -> TaskContext:
    result = deepcopy(pinned) if pinned else TaskContext(goal=message)
    result.history = []
    _bootstrap(result)
    previous_constraints = set(result.constraints)
    new_goal = _NEW_GOAL.fullmatch(message.strip())
    if new_goal:
        result = revise_context(result, goal=new_goal.group(1))
        history = []
        _ingest(result, new_goal.group(1))
    else:
        # 已有持久状态时只吸收本轮，不能把窗口中的旧约束反复复活。
        if pinned is None:
            for entry in history:
                if entry.get("role") == "user":
                    _ingest(result, entry.get("content", ""))
        _ingest(result, message)
        _retire_derived_goal(result, previous_constraints - set(result.constraints))
        if (
            pinned is not None
            and result.epoch == pinned.epoch
            and (
                result.goal != pinned.goal
                or result.constraints != pinned.constraints
                or result.goal_needs_input != pinned.goal_needs_input
            )
        ):
            _new_epoch(result)

    def size() -> int:
        return len(json.dumps(result.messages(), ensure_ascii=False).encode())

    if size() > max_bytes:
        raise ContextOverflowError("关键约束超出上下文预算，请拆分任务；未丢弃约束")
    terms = set(re.findall(r"[a-z0-9_-]{2,}", message.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", message):
        terms.update(phrase[i : i + 2] for i in range(max(0, len(phrase) - 1)))
    retired = {r["text"] for r in result.constraint_records if r["status"] != "active"}
    ranked = sorted(
        enumerate(history),
        key=lambda pair: (sum(term in pair[1].get("content", "").lower() for term in terms), pair[0]),
        reverse=True,
    )
    chosen = []
    for index, entry in ranked:
        if entry.get("role") not in {"user", "assistant"}:
            continue
        # 没有当前epoch来源的旧消息不能越过编辑边界，包括助手复述。
        if result.epoch and entry.get("_context_epoch") != result.epoch:
            continue
        if entry.get("role") == "user" and entry.get("content", "").strip() in retired:
            continue
        candidate = {"role": entry["role"], "content": entry.get("content", "")}
        result.history.append(candidate)
        if size() <= max_bytes:
            chosen.append((index, candidate))
        else:
            result.history.pop()
    result.history = [entry for _, entry in sorted(chosen)]
    return result


async def _lock_context(conn, conversation_id: UUID, *, write: bool = True):
    from shared.context import current_user_email

    lock = "UPDATE" if write else "SHARE"
    uid = await conn.fetchval(
        "SELECT c.user_id FROM conversations c JOIN users u ON u.id=c.user_id "
        f"WHERE c.id=$1 AND u.email=$2 FOR {lock} OF c",
        conversation_id,
        current_user_email.get(""),
    )
    if uid is None:
        raise PermissionError("无法访问该会话")
    row = await conn.fetchrow(
        f"SELECT snapshot,revision FROM conversation_contexts WHERE conversation_id=$1 FOR {lock}",
        conversation_id,
    )
    return uid, row


def _decode_context(row) -> TaskContext | None:
    if not row:
        return None
    raw = row["snapshot"]
    return TaskContext(**(json.loads(raw) if isinstance(raw, str) else raw))


async def _epoch_history(conn, conversation_id: UUID, context: TaskContext, *, exclude_message_id: UUID | None = None):
    since = datetime.fromisoformat(context.history_since) if context.history_since else None
    rows = await conn.fetch(
        "SELECT role,content FROM (SELECT id,role,content,created_at FROM messages "
        "WHERE conversation_id=$1 AND ($2::timestamptz IS NULL OR created_at >= $2) "
        "AND COALESCE(metadata->>'context_epoch','0')=$3 "
        "AND ($4::uuid IS NULL OR id <> $4) ORDER BY created_at DESC,id DESC LIMIT 50) recent "
        "ORDER BY created_at,id",
        conversation_id,
        since,
        str(context.epoch),
        exclude_message_id,
    )
    return [
        {"role": row["role"], "content": row["content"], "_context_epoch": context.epoch}
        for row in rows
        if row["role"] in {"user", "assistant"} and row["content"]
    ]


async def persistent_context(
    message: str,
    history: list[dict[str, str]],
    conversation_id: str | None,
    *,
    max_bytes: int = 16000,
    message_id: str | None = None,
) -> TaskContext:
    """按conversation→snapshot顺序加锁；只有真实用户入口可吸收本轮内容。"""
    from shared.context import current_user_email
    from shared.db import get_pool

    if not conversation_id or not current_user_email.get(""):
        return build_context(message, history, max_bytes=max_bytes)
    cid = UUID(conversation_id)
    mid = UUID(message_id) if message_id else None
    async with get_pool().acquire() as conn, conn.transaction():
        uid, row = await _lock_context(conn, cid)
        pinned = _decode_context(row)
        message_time = None
        if mid is not None:
            message_time = await conn.fetchval(
                "SELECT created_at FROM messages WHERE id=$1 AND conversation_id=$2 AND role='user'",
                mid,
                cid,
            )
            if message_time is None:
                raise ValueError("当前用户消息不存在")
            if pinned and pinned.history_since and message_time < datetime.fromisoformat(pinned.history_since):
                raise ContextEpochMismatchError("会话目标或约束已更新，请按最新状态重新发送")
        context = build_context(message, history, max_bytes=max_bytes, pinned=pinned)
        if context.epoch != (pinned.epoch if pinned else 0) or (context.epoch and not context.history_since):
            boundary = message_time or await conn.fetchval("SELECT clock_timestamp()")
            context.history_since = boundary.isoformat()
        if mid is not None:
            await conn.execute(
                "UPDATE messages SET metadata=COALESCE(metadata,'{}'::jsonb) || "
                "jsonb_build_object('context_epoch',$2::bigint) WHERE id=$1",
                mid,
                context.epoch,
            )
        if context.epoch:
            current_history = await _epoch_history(conn, cid, context, exclude_message_id=mid)
            context = build_context("", current_history, max_bytes=max_bytes, pinned=context)
        snapshot = context.state()
        snapshot["history"] = []
        await conn.execute(
            "INSERT INTO conversation_contexts(conversation_id,user_id,snapshot) VALUES($1,$2,$3::jsonb) "
            "ON CONFLICT(conversation_id) DO UPDATE SET snapshot=EXCLUDED.snapshot, "
            "revision=conversation_contexts.revision+1,updated_at=NOW()",
            cid,
            uid,
            json.dumps(snapshot, ensure_ascii=False),
        )
    current_context_binding.set((str(cid), context.epoch))
    return context


async def specialist_context(
    conversation_id: str, fallback_history: list[dict] | None, *, expected_epoch: int | None = None
) -> list[dict] | None:
    """专业端只读投影权威状态；绝不把模型生成的A2A消息写成用户要求。"""
    from shared.config import settings
    from shared.db import get_pool

    cid = UUID(conversation_id)
    async with get_pool().acquire() as conn, conn.transaction():
        _, row = await _lock_context(conn, cid, write=False)
        context = _decode_context(row)
        if context is None:
            if expected_epoch not in {None, 0}:
                raise ContextEpochMismatchError("会话上下文版本已改变")
            return fallback_history
        if expected_epoch is not None and expected_epoch != context.epoch:
            raise ContextEpochMismatchError("会话目标或约束已更新；未执行旧请求")
        if context.goal_needs_input:
            raise ContextNeedsInputError("原目标包含已撤销的要求，请先明确当前目标")
        history = await _epoch_history(conn, cid, context)
        return build_context("", history, pinned=context, max_bytes=settings.CONTEXT_MAX_BYTES).messages()


async def remember_evidence(conversation_id: str, steps: list[dict], *, expected_epoch: int | None = None) -> bool:
    """同一epoch的服务端工具引用才可写回；旧运行不能恢复已撤销的证据。"""
    from shared.db import get_pool

    binding = current_context_binding.get()
    if expected_epoch is None and binding and binding[0] == conversation_id:
        expected_epoch = binding[1]
    if expected_epoch is None:
        return False
    allowed = {
        "get_product_details",
        "search_products",
        "compare_products",
        "check_stock",
        "get_order_details",
        "get_user_orders",
        "get_return_operation_status",
    }
    references = []
    for step in steps:
        tool = step.get("tool_name")
        provenance = step.get("provenance") or {}
        if tool not in allowed or step.get("status") != "success" or provenance.get("source") != f"tool:{tool}":
            continue
        for value in provenance.get("row_ids", []):
            try:
                references.append(f"{tool}:{UUID(str(value))}")
            except ValueError:
                continue
    if not references:
        return False
    cid = UUID(conversation_id)
    async with get_pool().acquire() as conn, conn.transaction():
        _, row = await _lock_context(conn, cid)
        context = _decode_context(row)
        if context is None or context.epoch != expected_epoch:
            return False
        context.resolved_entities = list(dict.fromkeys([*context.resolved_entities, *references]))
        context.evidence_refs = context.resolved_entities.copy()
        await conn.execute(
            "UPDATE conversation_contexts SET snapshot=$2::jsonb,revision=revision+1,updated_at=NOW() "
            "WHERE conversation_id=$1 AND revision=$3",
            cid,
            json.dumps(context.state(), ensure_ascii=False),
            row["revision"],
        )
        return True


async def prepare_chat_context(
    message: str,
    history: list[dict[str, str]],
    conversation_id: str | None,
    *,
    message_id: str | None = None,
) -> tuple[list[dict[str, str]], bool]:
    """普通和SSE用户入口共用；记录本轮epoch供A2A与完成回写校验。"""
    from shared.config import settings
    from shared.context import current_user_email
    from shared.db import get_pool

    current_context_binding.set(None)
    enabled = settings.DECISION_CONTEXT_ENABLED
    if not enabled and conversation_id and current_user_email.get(""):
        enabled = bool(
            await get_pool().fetchval(
                "SELECT EXISTS(SELECT 1 FROM conversation_contexts s JOIN users u ON u.id=s.user_id "
                "WHERE s.conversation_id=$1 AND u.email=$2)",
                UUID(conversation_id),
                current_user_email.get(""),
            )
        )
    if not enabled:
        # 即使旧轮没有上下文，epoch=0也能阻止它在之后的显式编辑后重新注入历史。
        if conversation_id:
            current_context_binding.set((conversation_id, 0))
        return history, False
    context = await persistent_context(
        message,
        history,
        conversation_id,
        max_bytes=settings.CONTEXT_MAX_BYTES,
        message_id=message_id,
    )
    if context.goal_needs_input:
        raise ContextNeedsInputError("原目标包含已撤销的要求，请先在当前任务中明确新目标")
    return context.messages(), True
