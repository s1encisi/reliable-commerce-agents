"""向后兼容入口，实际实现已迁移到 shared.factory。

保留 create_chat_client、create_embedding_client、get_embedding_model
的导出，兼容评测、智能体宿主和编排器等既有调用方。新代码直接从
shared.factory 导入。
"""

from __future__ import annotations

from shared.factory import (
    create_chat_client,
    create_embedding_client,
    get_chat_client,
    get_embedding_model,
    get_embeddings_client,
)

__all__ = [
    "create_chat_client",
    "create_embedding_client",
    "get_chat_client",
    "get_embedding_model",
    "get_embeddings_client",
]
