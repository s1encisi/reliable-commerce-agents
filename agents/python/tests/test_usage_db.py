"""用量记录测试。

纯逻辑覆盖 JSON 序列化和计时；真实 PostgreSQL 覆盖运行用量及
执行步骤写入。
"""

from __future__ import annotations

import json
import time
from uuid import UUID

import asyncpg
import pytest
import pytest_asyncio

import shared.db as shared_db
from shared.usage_db import UsageTimer, _safe_json, log_agent_usage, log_execution_step


def test_safe_json_serializes_dict() -> None:
    assert json.loads(_safe_json({"a": 1, "b": "x"})) == {"a": 1, "b": "x"}


def test_safe_json_none_returns_none() -> None:
    assert _safe_json(None) is None


def test_safe_json_coerces_unknown_objects() -> None:
    class Thing:
        def __str__(self) -> str:
            return "thing-repr"

    # default=str 让其他值也可序列化。
    assert "thing-repr" in _safe_json({"obj": Thing()})


def test_safe_json_unserializable_returns_error_marker() -> None:
    # 不支持的字典键触发 JSON 编码异常分支。
    assert json.loads(_safe_json({(1, 2): "tuple-key"})) == {"error": "unserializable"}


def test_usage_timer_measures_nonnegative_duration() -> None:
    with UsageTimer() as timer:
        time.sleep(0.001)
    assert isinstance(timer.duration_ms, int)
    assert timer.duration_ms >= 0


def test_usage_timer_zero_before_exit() -> None:
    timer = UsageTimer()
    assert timer.duration_ms == 0


# ─────────────────────── DB-backed insert tests ───────────────────────


@pytest_asyncio.fixture
async def db_pool(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    """注入测试连接池，供日志写入函数使用。"""
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


@pytest.mark.asyncio
async def test_log_agent_usage_returns_uuid(db_pool: asyncpg.Pool) -> None:
    result = await log_agent_usage(
        user_id=None,
        agent_name="product-discovery",
        input_summary="find headphones",
        tokens_in=100,
        tokens_out=200,
        tool_calls_count=2,
        duration_ms=350,
        status="success",
    )
    assert isinstance(result, UUID), f"Expected UUID, got {type(result)}: {result}"


@pytest.mark.asyncio
async def test_log_agent_usage_persists_row(db_pool: asyncpg.Pool) -> None:
    usage_id = await log_agent_usage(
        user_id=None,
        agent_name="order-management",
        input_summary="cancel order",
        tokens_in=50,
        tokens_out=80,
        status="success",
    )
    assert usage_id is not None
    row = await db_pool.fetchrow("SELECT * FROM usage_logs WHERE id = $1", usage_id)
    assert row is not None
    assert row["agent_name"] == "order-management"
    assert row["tokens_in"] == 50
    assert row["status"] == "success"


@pytest.mark.asyncio
async def test_log_execution_step_persists_row(db_pool: asyncpg.Pool) -> None:
    usage_id = await log_agent_usage(
        user_id=None,
        agent_name="review-sentiment",
        status="success",
    )
    assert usage_id is not None

    await log_execution_step(
        usage_log_id=usage_id,
        step_index=0,
        tool_name="get_product_reviews",
        tool_input={"product_id": "abc-123"},
        tool_output={"reviews": []},
        status="success",
        duration_ms=42,
    )

    row = await db_pool.fetchrow(
        "SELECT * FROM agent_execution_steps WHERE usage_log_id = $1",
        usage_id,
    )
    assert row is not None
    assert row["tool_name"] == "get_product_reviews"
    assert row["step_index"] == 0
    assert row["duration_ms"] == 42


@pytest.mark.asyncio
async def test_log_agent_usage_truncates_long_summary(db_pool: asyncpg.Pool) -> None:
    long_summary = "x" * 1000
    usage_id = await log_agent_usage(
        user_id=None,
        agent_name="orchestrator",
        input_summary=long_summary,
        status="success",
    )
    assert usage_id is not None
    row = await db_pool.fetchrow("SELECT input_summary FROM usage_logs WHERE id = $1", usage_id)
    assert len(row["input_summary"]) <= 500
