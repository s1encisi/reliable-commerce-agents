"""后端测试共用夹具。

数据库与 Redis 测试通过 testcontainers 使用真实隔离容器；会话级
复用容器，每例清空业务状态。模型调用默认使用预设响应，禁止普通
测试意外访问真实模型。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
import redis.asyncio as redis_asyncio
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

REPO_ROOT = Path(__file__).resolve().parents[3]
INIT_SQL = REPO_ROOT / "docker" / "postgres" / "init.sql"


def pytest_configure(config: pytest.Config) -> None:
    """框架 __init__.py 被清空时立即给出明确错误。

    Linux 下 uv 的安装文件可能与缓存硬链接；旧补丁原地写入会同时破坏
    缓存，单纯重装也可能继续链接坏文件。该检查避免在无关测试中才出现
    难以定位的 ImportError。
    """
    import agent_framework

    init_path = Path(agent_framework.__file__)
    if init_path.read_text().strip() == "":
        pytest.exit(
            f"agent_framework's __init__.py is empty ({init_path}) — the "
            "installed package is broken. Recover with:\n"
            "  cd agents/python\n"
            "  uv cache clean agent-framework-core\n"
            "  uv sync --reinstall-package agent-framework-core --all-packages --extra dev",
            returncode=1,
        )


# ─────────────────────── Postgres fixture ───────────────────────


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    """每个测试会话复用一个隔离 PostgreSQL 容器。"""
    container = PostgresContainer("pgvector/pgvector:pg16", dbname="ecommerce_test")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    url = postgres_container.get_connection_url()
    # testcontainers 返回 psycopg URL，asyncpg 需要普通 postgresql://。
    return url.replace("postgresql+psycopg2://", "postgresql://")


@pytest_asyncio.fixture(scope="session")
async def _schema_applied(database_url: str) -> None:
    """每个会话应用一次 docker/postgres/init.sql 的实际表结构。"""
    sql = INIT_SQL.read_text()
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def postgres_pool(
    database_url: str,
    _schema_applied: None,
) -> AsyncGenerator[asyncpg.Pool, None]:
    """连接共享测试容器的 asyncpg 池。"""
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def clean_db(postgres_pool: asyncpg.Pool) -> AsyncGenerator[asyncpg.Pool, None]:
    """测试前清空数据表但保留结构，供修改数据库的用例使用。"""
    async with postgres_pool.acquire() as conn:
        tables = await conn.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename NOT LIKE 'pg_%'
            """
        )
        if tables:
            names = ", ".join(f'"{row["tablename"]}"' for row in tables)
            await conn.execute(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
    yield postgres_pool


# ─────────────────────── Redis fixture ───────────────────────────


@pytest.fixture(scope="session")
def redis_container() -> Generator[RedisContainer, None, None]:
    """每个测试会话复用一个 Redis 容器，与数据库夹具一致。"""
    container = RedisContainer("redis:7-alpine")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest_asyncio.fixture
async def redis_client(redis_container: RedisContainer) -> AsyncGenerator[redis_asyncio.Redis, None]:
    """连接真实测试 Redis 的异步客户端，每例开始前清空。

    RedisContainer 默认返回同步客户端，这里按相同主机端口创建
    redis.asyncio.Redis，与实际限流实现保持一致。
    """
    client = redis_asyncio.Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
        decode_responses=True,
    )
    try:
        await client.flushdb()
        yield client
    finally:
        await client.aclose()


# ─────────────────────── Fake LLM fixtures ──────────────────────


class FakeChatClient:
    """确定性的 MAF 客户端替身；排队预设响应并记录输入，不访问真实模型。"""

    def __init__(self) -> None:
        self._responses: list[str] = []
        self.call_count: int = 0
        self.received_prompts: list[list[dict[str, Any]]] = []

    def enqueue(self, *responses: str) -> FakeChatClient:
        self._responses.extend(responses)
        return self

    async def complete(self, messages: list[dict[str, Any]], **_kwargs: Any) -> str:
        self.call_count += 1
        self.received_prompts.append(messages)
        if not self._responses:
            raise RuntimeError("FakeChatClient has no enqueued responses. Call enqueue(...) before invoking.")
        return self._responses.pop(0)


@pytest.fixture
def fake_chat_client() -> FakeChatClient:
    return FakeChatClient()


# ─────────────────────── Canary fixture ─────────────────────────


@pytest.fixture
def sample_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """服务必需的最小环境变量集，各测试可用 monkeypatch 补充。

    避免测试意外读取开发者的真实 .env。
    """
    env = {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "test-key",
        "LLM_MODEL": "gpt-4.1",
        "JWT_SECRET": "test-secret-" + "0" * 48,
        "AGENT_SHARED_SECRET": "test-agent-secret",
        "OTEL_ENABLED": "false",
        "DATABASE_URL": os.environ.get("DATABASE_URL", "postgresql://test:test@localhost/test"),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env
