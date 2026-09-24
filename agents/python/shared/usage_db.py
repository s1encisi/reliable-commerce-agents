"""用量日志：记录智能体调用与执行步骤。

写入 usage_logs 和 agent_execution_steps，附加当前 OTel trace_id，
用于关联 Jaeger 追踪。
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from shared.db import get_pool
from shared.telemetry import get_current_trace_id

logger = logging.getLogger(__name__)


async def log_agent_usage(
    user_id: UUID | str | None,
    agent_name: str,
    session_id: UUID | str | None = None,
    input_summary: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    tool_calls_count: int = 0,
    duration_ms: int = 0,
    status: str = "success",
    error_message: str | None = None,
) -> UUID | None:
    """向 usage_logs 插入记录，返回用量日志标识。"""
    pool = get_pool()
    trace_id = get_current_trace_id()

    try:
        row = await pool.fetchrow(
            """INSERT INTO usage_logs
               (user_id, agent_name, session_id, trace_id, input_summary,
                tokens_in, tokens_out, tool_calls_count, duration_ms, status, error_message)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
               RETURNING id""",
            str(user_id) if user_id else None,
            agent_name,
            str(session_id) if session_id else None,
            trace_id,
            input_summary[:500] if input_summary else None,
            tokens_in,
            tokens_out,
            tool_calls_count,
            duration_ms,
            status,
            error_message,
        )
        return row["id"] if row else None
    except Exception:
        logger.exception("Failed to log agent usage for %s", agent_name)
        return None


async def log_execution_step(
    usage_log_id: UUID | str,
    step_index: int,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
    tool_output: dict[str, Any] | None = None,
    status: str = "success",
    duration_ms: int = 0,
) -> None:
    """向 agent_execution_steps 插入记录。"""
    pool = get_pool()
    try:
        await pool.execute(
            """INSERT INTO agent_execution_steps
               (usage_log_id, step_index, tool_name, tool_input, tool_output, status, duration_ms)
               VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7)""",
            str(usage_log_id),
            step_index,
            tool_name,
            _safe_json(tool_input),
            _safe_json(tool_output),
            status,
            duration_ms,
        )
    except Exception:
        logger.exception("Failed to log execution step %s for %s", step_index, tool_name)


class UsageTimer:
    """记录智能体调用耗时的上下文管理器。"""

    def __init__(self) -> None:
        self._start: float = 0
        self.duration_ms: int = 0

    def __enter__(self) -> UsageTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: Any) -> None:
        self.duration_ms = int((time.perf_counter() - self._start) * 1000)


def _safe_json(data: dict | None) -> str | None:
    """安全地把字典序列化为 asyncpg JSONB 所需的 JSON 字符串。"""
    if data is None:
        return None
    import json

    try:
        return json.dumps(data, default=str)
    except (TypeError, ValueError):
        return json.dumps({"error": "unserializable"})
