"""MAF 会话与可替换历史存储后端。

AgentSession 保存会话标识与状态；HistoryProvider 负责历史读写。
postgres 复用 conversations/messages，file 在 MAF_SESSION_DIR
保存 JSONL，memory 使用进程内字典。通过 get_history_provider 选择，
并用 session_from_id 构建与业务会话关联的 AgentSession。
"""

import json
import logging
from pathlib import Path
from typing import Any

from agent_framework import AgentSession, Message
from agent_framework._sessions import HistoryProvider
from agent_framework._types import Role

from shared import config as _config

logger = logging.getLogger(__name__)


def _settings():
    """延迟读取当前 settings 绑定。

    测试可能通过 importlib.reload 重建 shared.config；每次从模块获取
    配置，避免持有旧对象。
    """
    return _config.settings


# ─────────────────────── Backends ───────────────────────


class InMemorySessionHistoryProvider(HistoryProvider):
    """进程内临时会话存储，主要用于测试。"""

    def __init__(self, source_id: str = "memory-history") -> None:
        super().__init__(source_id)
        self._store: dict[str, list[Message]] = {}

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **_: Any,
    ) -> list[Message]:
        if not session_id:
            return []
        return list(self._store.get(session_id, []))

    async def save_messages(
        self,
        session_id: str | None,
        messages,
        *,
        state: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        if not session_id:
            return
        bucket = self._store.setdefault(session_id, [])
        bucket.extend(messages)


class FileSessionHistoryProvider(HistoryProvider):
    """每会话一个 JSONL 文件，适合无数据库的开发环境。"""

    def __init__(self, directory: str | Path, source_id: str = "file-history") -> None:
        super().__init__(source_id)
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe = session_id.replace("/", "_")
        return self._dir / f"{safe}.jsonl"

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **_: Any,
    ) -> list[Message]:
        if not session_id:
            return []
        path = self._path(session_id)
        if not path.exists():
            return []
        messages: list[Message] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            messages.append(Message.from_dict(data))
        return messages

    async def save_messages(
        self,
        session_id: str | None,
        messages,
        *,
        state: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        if not session_id:
            return
        path = self._path(session_id)
        with path.open("a", encoding="utf-8") as fh:
            for msg in messages:
                fh.write(json.dumps(msg.to_dict()))
                fh.write("\n")


class PostgresSessionHistoryProvider(HistoryProvider):
    """既有 conversations/messages 表的历史适配器。

    conversation_id 等于调用方传入的会话 UUID；保存角色与首个文本内容，
    兼容界面及 A2A 消息格式。连接池由构造函数注入，便于测试替换。
    """

    def __init__(self, pool, *, source_id: str = "postgres-history", max_history: int = 50) -> None:
        super().__init__(source_id)
        self._pool = pool
        self._max_history = max_history

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **_: Any,
    ) -> list[Message]:
        if not session_id:
            return []
        async with self._pool.acquire() as conn:
            # 在原表直接按时间正序加 LIMIT，
            # 取到的是最早 max_history 条记录，
            # 会在长会话中丢失近期上下文，
            # 使追问无法获得需要的信息。
            # 应先取最近 max_history 条，
            # 再恢复为调用方所需的时间正序。
            rows = await conn.fetch(
                """
                SELECT role, content FROM (
                    SELECT role, content, created_at
                    FROM messages
                    WHERE conversation_id = $1::uuid
                    ORDER BY created_at DESC
                    LIMIT $2
                ) recent
                ORDER BY created_at ASC
                """,
                session_id,
                self._max_history,
            )
        return [Message(role=Role(row["role"]), contents=[str(row["content"])]) for row in rows]

    async def save_messages(
        self,
        session_id: str | None,
        messages,
        *,
        state: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        if not session_id or not messages:
            return
        async with self._pool.acquire() as conn:
            for msg in messages:
                content = msg.text or ""
                if not content:
                    continue
                await conn.execute(
                    """
                    INSERT INTO messages (conversation_id, role, content)
                    VALUES ($1::uuid, $2, $3)
                    """,
                    session_id,
                    str(msg.role),
                    content,
                )


# ─────────────────────── Factory ───────────────────────


def get_history_provider(*, pool: Any = None) -> HistoryProvider:
    """按 MAF_SESSION_BACKEND 返回历史提供器。

    postgres 需要连接池，file 使用 MAF_SESSION_DIR，memory 仅临时保存。
    """
    settings = _settings()
    backend = (settings.MAF_SESSION_BACKEND or "postgres").lower()
    if backend == "postgres":
        if pool is None:
            raise ValueError(
                "PostgresSessionHistoryProvider requires an asyncpg pool. "
                "Pass pool= explicitly or use MAF_SESSION_BACKEND=file|memory for local dev."
            )
        return PostgresSessionHistoryProvider(pool)
    if backend == "file":
        return FileSessionHistoryProvider(settings.MAF_SESSION_DIR)
    if backend == "memory":
        return InMemorySessionHistoryProvider()
    raise ValueError(f"Unknown MAF_SESSION_BACKEND: {backend}")


def session_from_id(session_id: str | None) -> AgentSession:
    """按已有会话标识构建 AgentSession；为空则生成新标识。"""
    return AgentSession(session_id=session_id) if session_id else AgentSession()


async def get_history_as_dicts(provider: HistoryProvider, session_id: str | None) -> list[dict[str, str]]:
    """读取历史并展平为 role/content 字典，供应用各路径传递。

    这里只替换读取逻辑，不把历史提供器自动挂到编排器 context_providers。
    自动挂载会在每轮额外保存消息，与路由现有的含智能体、元数据和执行
    时间线的写入重复。
    """
    messages = await provider.get_messages(session_id)
    return [{"role": str(m.role), "content": m.text} for m in messages if m.text]
