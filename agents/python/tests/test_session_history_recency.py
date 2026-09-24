"""长会话必须保留最近历史的真实数据库回归测试。

直接 ASC LIMIT 会取最早记录，遗漏追问所需上下文。模拟池只返回
预设行无法发现排序错误，因此这里使用真实 PostgreSQL。
"""

from __future__ import annotations

import uuid

import pytest

from shared.agent_host import _rehydrate_history_from_session
from shared.context import current_user_email
from shared.session import PostgresSessionHistoryProvider


async def _seed_conversation(clean_db, *, total_messages: int) -> tuple[str, str]:
    user_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        f"recency-{user_id}@example.com",
    )
    email = f"recency-{user_id}@example.com"
    conv_row = await clean_db.fetchrow(
        "INSERT INTO conversations (user_id, title) VALUES ($1, 'test') RETURNING id",
        user_id,
    )
    conversation_id = str(conv_row["id"])

    for i in range(total_messages):
        role = "user" if i % 2 == 0 else "assistant"
        await clean_db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES ($1, $2, $3)",
            conversation_id,
            role,
            f"message {i}",
        )
    return conversation_id, email


@pytest.mark.asyncio
async def test_postgres_history_provider_keeps_most_recent_messages_when_over_limit(clean_db) -> None:
    conversation_id, _ = await _seed_conversation(clean_db, total_messages=10)

    provider = PostgresSessionHistoryProvider(clean_db, max_history=4)
    messages = await provider.get_messages(conversation_id)

    texts = [m.text for m in messages]
    assert texts == ["message 6", "message 7", "message 8", "message 9"], (
        "expected the 4 most recent messages in chronological order, got: " + str(texts)
    )


@pytest.mark.asyncio
async def test_rehydrate_history_keeps_most_recent_messages_when_over_limit(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shared.db.get_pool", lambda: clean_db)
    monkeypatch.setattr("shared.agent_host._SESSION_HISTORY_LIMIT", 4)

    conversation_id, email = await _seed_conversation(clean_db, total_messages=10)
    # 历史读取按当前用户归属隔离，
    # 专业智能体从 x-user-email 设置该 ContextVar。
    current_user_email.set(email)

    history = await _rehydrate_history_from_session(conversation_id)

    texts = [h["content"] for h in history]
    assert texts == ["message 6", "message 7", "message 8", "message 9"], (
        "expected the 4 most recent messages in chronological order, got: " + str(texts)
    )
