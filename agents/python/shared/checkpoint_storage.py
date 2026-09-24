"""基于 PostgreSQL 的 MAF 检查点存储，用于持久化工作流状态。

读写 init.sql 定义的 workflow_checkpoints 表。检查点由 MAF 的
encode_checkpoint_value 编码为 JSONB，格式与 FileCheckpointStorage
一致，只是存储介质由文件改为数据库。

MAF_CHECKPOINT_BACKEND=postgres 时，由 shared.factory 创建。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import asyncpg
from agent_framework._workflows._checkpoint import (
    CheckpointID,
    CheckpointStorage,
    WorkflowCheckpoint,
    WorkflowCheckpointException,
)
from agent_framework._workflows._checkpoint_encoding import (
    decode_checkpoint_value,
    encode_checkpoint_value,
)

logger = logging.getLogger(__name__)


class PostgresCheckpointStorage(CheckpointStorage):
    """使用 asyncpg 与 JSONB 实现 CheckpointStorage。

    参数：
        pool：应用共享的 asyncpg 连接池。
        table：表名，默认 workflow_checkpoints；仅测试时覆盖。
    """

    def __init__(self, pool: asyncpg.Pool, *, table: str = "workflow_checkpoints") -> None:
        self._pool = pool
        self._table = table

    async def save(self, checkpoint: WorkflowCheckpoint) -> CheckpointID:
        payload = encode_checkpoint_value(checkpoint.to_dict())
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._table} (checkpoint_id, workflow_name, payload, created_at)
                VALUES ($1, $2, $3::jsonb, $4)
                ON CONFLICT (checkpoint_id)
                DO UPDATE SET payload = EXCLUDED.payload, created_at = EXCLUDED.created_at
                """,
                checkpoint.checkpoint_id,
                checkpoint.workflow_name,
                json.dumps(payload),
                _parse_ts(checkpoint.timestamp),
            )
        logger.debug("saved checkpoint %s for workflow %s", checkpoint.checkpoint_id, checkpoint.workflow_name)
        return checkpoint.checkpoint_id

    async def load(self, checkpoint_id: CheckpointID) -> WorkflowCheckpoint:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT payload FROM {self._table} WHERE checkpoint_id = $1",
                checkpoint_id,
            )
        if row is None:
            raise WorkflowCheckpointException(f"No checkpoint found with ID {checkpoint_id}")
        data = decode_checkpoint_value(
            json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        )
        return WorkflowCheckpoint.from_dict(data)

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT payload FROM {self._table} WHERE workflow_name = $1 ORDER BY created_at DESC",
                workflow_name,
            )
        return [WorkflowCheckpoint.from_dict(decode_checkpoint_value(_payload(r))) for r in rows]

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[CheckpointID]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT checkpoint_id FROM {self._table} WHERE workflow_name = $1 ORDER BY created_at DESC",
                workflow_name,
            )
        return [str(r["checkpoint_id"]) for r in rows]

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT payload FROM {self._table} WHERE workflow_name = $1 ORDER BY created_at DESC LIMIT 1",
                workflow_name,
            )
        if row is None:
            return None
        return WorkflowCheckpoint.from_dict(decode_checkpoint_value(_payload(row)))

    async def delete(self, checkpoint_id: CheckpointID) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {self._table} WHERE checkpoint_id = $1",
                checkpoint_id,
            )
        # asyncpg 返回 DELETE 1 一类命令标签；尾部数字是影响行数。
        affected = int(result.split()[-1]) if result else 0
        return affected > 0


class RecordingCheckpointStorage(CheckpointStorage):
    """包装检查点存储，按保存顺序记录 checkpoint_id。

    MAF 在每个超步保存检查点，但 WorkflowEvent 流不提供对应保存事件。
    调用方可在迭代间读取 saved 列表并生成自己的 checkpoint 编排事件。
    保存操作与读取循环在同一任务执行，因此不会漏记或重复处理中途保存。
    """

    def __init__(self, inner: CheckpointStorage) -> None:
        self._inner = inner
        self.saved: list[CheckpointID] = []

    async def save(self, checkpoint: WorkflowCheckpoint) -> CheckpointID:
        checkpoint_id = await self._inner.save(checkpoint)
        self.saved.append(checkpoint_id)
        return checkpoint_id

    async def load(self, checkpoint_id: CheckpointID) -> WorkflowCheckpoint:
        return await self._inner.load(checkpoint_id)

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        return await self._inner.list_checkpoints(workflow_name=workflow_name)

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[CheckpointID]:
        return await self._inner.list_checkpoint_ids(workflow_name=workflow_name)

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        return await self._inner.get_latest(workflow_name=workflow_name)

    async def delete(self, checkpoint_id: CheckpointID) -> bool:
        return await self._inner.delete(checkpoint_id)


def drain_new_checkpoint_ids(recorder: RecordingCheckpointStorage, already_seen: int) -> list[CheckpointID]:
    """返回 saved 中 already_seen 索引之后的新检查点标识。

    调用方用返回列表长度累加 already_seen，供下次查询使用。
    """
    return recorder.saved[already_seen:]


# ─────────────────────── Helpers ───────────────────────


def _payload(row: asyncpg.Record) -> dict:
    """兼容驱动将 JSONB 返回为字典或字符串的两种情况。"""
    value = row["payload"]
    return json.loads(value) if isinstance(value, str) else value


def _parse_ts(ts: str) -> datetime:
    """将 WorkflowCheckpoint 的 ISO-8601 时间戳转为 TIMESTAMPTZ 可用值。"""
    return datetime.fromisoformat(ts)
