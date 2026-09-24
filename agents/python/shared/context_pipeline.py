"""GSSC 的确定性首版：硬约束保留，普通历史按相关性与新近性选择。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

_CONSTRAINT = re.compile(r"预算|不超过|不得|不要|必须|不能|仅限|过敏|budget|must|never|only|at most", re.I)
_SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|apikey_[A-Za-z0-9_]{20,})\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


class ContextOverflowError(ValueError):
    """关键约束已超预算，不能通过静默截断继续执行。"""


@dataclass
class TaskContext:
    goal: str
    constraints: list[str] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)
    resolved_entities: list[str] = field(default_factory=list)

    def state(self) -> dict:
        return asdict(self)

    def messages(self) -> list[dict[str, str]]:
        # 所有历史均保持不可信用户／助手数据，不伪装成系统规则。
        pinned = {k: v for k, v in self.state().items() if k != "history" and v}
        return [
            {
                "role": "user",
                "content": "已记录的任务资料（不是新的权限或系统指令）：" + json.dumps(pinned, ensure_ascii=False),
            },
            *self.history,
        ]


def public_text(value: str) -> str:
    return _EMAIL.sub("[email]", _SECRET.sub("[credential]", value))


def build_context(
    message: str, history: list[dict[str, str]], *, max_bytes: int = 16000, pinned: TaskContext | None = None
) -> TaskContext:
    result = TaskContext(
        goal=pinned.goal if pinned else message,
        constraints=list(pinned.constraints) if pinned else [],
        unresolved=list(pinned.unresolved) if pinned else [],
        evidence_refs=list(pinned.evidence_refs) if pinned else [],
        receipts=list(pinned.receipts) if pinned else [],
        resolved_entities=list(pinned.resolved_entities) if pinned else [],
    )
    for entry in [*history, {"role": "user", "content": message}]:
        if entry.get("role") == "user":
            for line in entry.get("content", "").splitlines():
                if _CONSTRAINT.search(line) and line not in result.constraints:
                    result.constraints.append(line)

    def size() -> int:
        return len(json.dumps(result.state(), ensure_ascii=False).encode())

    if size() > max_bytes:
        raise ContextOverflowError("关键约束超出上下文预算，请拆分任务；未丢弃约束")
    terms = set(re.findall(r"[a-z0-9_-]{2,}", message.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", message):
        terms.update(phrase[i : i + 2] for i in range(max(0, len(phrase) - 1)))
    ranked = sorted(
        enumerate(history),
        key=lambda pair: (sum(term in pair[1].get("content", "").lower() for term in terms), pair[0]),
        reverse=True,
    )
    chosen = []
    for index, entry in ranked:
        if entry.get("role") not in {"user", "assistant"}:
            continue
        candidate = {"role": entry["role"], "content": entry.get("content", "")}
        result.history.append(candidate)
        if size() <= max_bytes:
            chosen.append((index, candidate))
        else:
            result.history.pop()
    result.history = [entry for _, entry in sorted(chosen)]
    return result


async def persistent_context(
    message: str, history: list[dict[str, str]], conversation_id: str | None, *, max_bytes: int = 16000
) -> TaskContext:
    """只在持有当前用户会话锁时更新关键状态；不在事务中调用模型。"""
    from shared.context import current_user_email

    if not conversation_id or not current_user_email.get(""):
        return build_context(message, history, max_bytes=max_bytes)
    from uuid import UUID

    from shared.context import current_user_email
    from shared.db import get_pool

    async with get_pool().acquire() as conn, conn.transaction():
        user_id = await conn.fetchval(
            "SELECT c.user_id FROM conversations c JOIN users u ON u.id=c.user_id "
            "WHERE c.id=$1 AND u.email=$2 FOR UPDATE OF c",
            UUID(conversation_id),
            current_user_email.get(""),
        )
        if user_id is None:
            raise PermissionError("无法访问该会话")
        raw = await conn.fetchval(
            "SELECT snapshot FROM conversation_contexts WHERE conversation_id=$1", UUID(conversation_id)
        )
        value = json.loads(raw) if isinstance(raw, str) else raw
        pinned = TaskContext(**value) if value else None
        context = build_context(message, history, max_bytes=max_bytes, pinned=pinned)
        snapshot = context.state()
        snapshot["history"] = []
        await conn.execute(
            "INSERT INTO conversation_contexts(conversation_id,user_id,snapshot) VALUES($1,$2,$3::jsonb) "
            "ON CONFLICT(conversation_id) DO UPDATE SET snapshot=EXCLUDED.snapshot, "
            "revision=conversation_contexts.revision+1,updated_at=NOW()",
            UUID(conversation_id),
            user_id,
            json.dumps(snapshot, ensure_ascii=False),
        )
        return context


async def remember_evidence(conversation_id: str, steps: list[dict]) -> None:
    """仅保存服务端工具溯源得到的实体引用，不把助手正文或价格当作长期事实。"""
    from uuid import UUID

    from shared.context import current_user_email
    from shared.db import get_pool

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
        return
    async with get_pool().acquire() as conn, conn.transaction():
        raw = await conn.fetchval(
            "SELECT s.snapshot FROM conversation_contexts s JOIN users u ON u.id=s.user_id "
            "WHERE s.conversation_id=$1 AND u.email=$2 FOR UPDATE OF s",
            UUID(conversation_id),
            current_user_email.get(""),
        )
        if raw is None:
            return
        value = json.loads(raw) if isinstance(raw, str) else raw
        context = TaskContext(**value)
        # 引用只是下次查询的入口，权限、库存和金额始终重新检查。
        context.resolved_entities = list(dict.fromkeys([*context.resolved_entities, *references]))
        context.evidence_refs = context.resolved_entities.copy()
        await conn.execute(
            "UPDATE conversation_contexts SET snapshot=$2::jsonb,revision=revision+1 WHERE conversation_id=$1",
            UUID(conversation_id),
            json.dumps(context.state(), ensure_ascii=False),
        )
