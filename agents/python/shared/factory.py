"""统一创建 MAF 客户端、存储后端和智能体注册表。

专业智能体、编排器和评测通过工厂获取依赖，使功能开关可替换实现而
不修改调用点。导出聊天客户端、向量嵌入客户端与模型名称、A2A 注册表
解析和缓存、会话存储以及检查点存储。存储类按需导入，降低测试开销。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import openai
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient

from shared.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────── Validators ───────────────────────


def _validate_openai() -> None:
    if not settings.OPENAI_API_KEY:
        raise ValueError(
            "OPENAI_API_KEY is required when LLM_PROVIDER=openai. Set it in .env or switch to LLM_PROVIDER=azure."
        )


def _validate_azure() -> None:
    missing = []
    if not settings.AZURE_OPENAI_ENDPOINT:
        missing.append("AZURE_OPENAI_ENDPOINT")
    if not settings.AZURE_OPENAI_KEY:
        missing.append("AZURE_OPENAI_KEY (or AZURE_OPENAI_API_KEY)")
    if not settings.AZURE_OPENAI_DEPLOYMENT:
        missing.append("AZURE_OPENAI_DEPLOYMENT (or AZURE_OPENAI_DEPLOYMENT_NAME)")
    if missing:
        raise ValueError(
            f"Azure OpenAI requires {', '.join(missing)}. Set them in .env or switch to LLM_PROVIDER=openai."
        )


# ─────────────────────── LLM clients ───────────────────────


def get_chat_client() -> OpenAIChatClient | OpenAIChatCompletionClient | Any:
    """按 LLM_PROVIDER 创建 MAF 聊天客户端。

    openai 使用 OpenAIChatClient，并尊重 LLM_BASE_URL；目标兼容端点
    必须支持实际使用的 API。azure 使用 OpenAIChatCompletionClient，
    通过 Chat Completions 接口调用。replay 使用 ReplayChatClient 读取
    已有夹具；只有 RECORD=true 时才经 REPLAY_RECORD_PROVIDER 录制。
    具体选择见前两章教程和 docs/architecture.md。
    """
    provider = settings.LLM_PROVIDER.lower()

    if provider in {"deepseek", "moonshot"}:
        return get_compatible_client(provider)

    if provider == "openai":
        host = urlparse(settings.LLM_BASE_URL or "").hostname
        if host in {"api.deepseek.com", "api.moonshot.cn", "api.typesafe.ai"}:
            raise ValueError("请使用专用提供方配置，兼容地址不能绕过累计预算")
        _validate_openai()
        logger.info("Creating OpenAI chat client (model=%s, base_url=%s)", settings.LLM_MODEL, settings.LLM_BASE_URL)
        return OpenAIChatClient(
            model=settings.LLM_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.LLM_BASE_URL or None,
        )

    if provider == "azure":
        _validate_azure()
        api_version = settings.AZURE_OPENAI_API_VERSION
        logger.info(
            "Creating Azure OpenAI chat-completions client (deployment=%s, endpoint=%s, api_version=%s)",
            settings.AZURE_OPENAI_DEPLOYMENT,
            settings.AZURE_OPENAI_ENDPOINT,
            api_version,
        )
        return OpenAIChatCompletionClient(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version=api_version,
        )

    if provider == "replay":
        from shared.replay_client import ReplayChatClient

        logger.info(
            "Creating replay chat client (record=%s, fixtures_dir=%s)", settings.RECORD, settings.REPLAY_FIXTURES_DIR
        )
        return ReplayChatClient(
            fixtures_dir=settings.REPLAY_FIXTURES_DIR,
            record=settings.RECORD,
            record_provider=settings.REPLAY_RECORD_PROVIDER,
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER: {settings.LLM_PROVIDER!r}. "
        "Must be 'openai', 'azure', 'replay', 'deepseek', or 'moonshot'."
    )


def get_embeddings_client() -> openai.AsyncOpenAI | openai.AsyncAzureOpenAI:
    """创建异步向量嵌入客户端，包括离线 replay 分支。

    回放模式不能落入需要密钥的 OpenAI 路径，否则模型可能仅使用普通
    商品搜索，掩盖 pgvector 检索从未真正执行的问题。
    """
    embedding_provider = settings.EMBEDDING_PROVIDER.lower()
    if embedding_provider == "auto":
        embedding_provider = settings.LLM_PROVIDER.lower()
    if embedding_provider in {"none", "deepseek", "moonshot"}:
        raise EmbeddingsUnavailableError("未配置独立嵌入提供方，请使用词法检索")
    if embedding_provider == "replay":
        from shared.replay_embeddings import ReplayEmbeddingsClient

        return ReplayEmbeddingsClient()  # type: ignore[return-value]

    if embedding_provider == "azure":
        _validate_azure()
        return openai.AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    _validate_openai()
    return openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def get_embedding_model() -> str:
    """返回提供方对应的向量嵌入模型或 Azure 部署名称。"""
    if settings.LLM_PROVIDER.lower() == "azure" and settings.AZURE_EMBEDDING_DEPLOYMENT:
        return settings.AZURE_EMBEDDING_DEPLOYMENT
    return settings.EMBEDDING_MODEL


# ─────────────────────── A2A registry ───────────────────────


def parse_agent_registry(raw: str | None) -> dict[str, str]:
    """把 AGENT_REGISTRY JSON 校验为名称到 URL 的映射。

    格式错误、空地址或缺少协议必须立即抛错，不能让服务通过健康检查后
    才在首次路由时失败。校验协议和主机，不强制端口；托管 HTTPS 端点
    可能不显式指定端口。
    """
    try:
        registry = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"AGENT_REGISTRY is not valid JSON ({exc.msg} at pos {exc.pos}). Got: {raw!r}") from exc

    if not isinstance(registry, dict):
        raise ValueError(
            f"AGENT_REGISTRY must decode to an object {{name: url}}, got {type(registry).__name__}: {registry!r}"
        )

    parsed: dict[str, str] = {}
    for name, url in registry.items():
        name, url = str(name), str(url).strip()
        if not url:
            raise ValueError(
                f"AGENT_REGISTRY entry {name!r} has an empty URL. An unresolved template output or "
                f"an unset variable usually looks like this."
            )
        bits = urlparse(url)
        if bits.scheme not in ("http", "https") or not bits.netloc:
            raise ValueError(f"AGENT_REGISTRY entry {name!r} must be an absolute http(s) URL, got {url!r}.")
        parsed[name] = url
    return parsed


@lru_cache(maxsize=1)
def get_agent_registry() -> dict[str, str]:
    """解析并缓存当前配置中的智能体注册表。

    测试若临时替换 settings.AGENT_REGISTRY，应直接调用
    parse_agent_registry，避免读取旧缓存。
    """
    return parse_agent_registry(settings.AGENT_REGISTRY)


# ─────────────────────── Session / checkpoint backends ─────


def get_session_storage() -> Any:
    """按 MAF_SESSION_BACKEND 选择会话存储入口。

    当前返回值与调用方的历史处理方式需结合实际分支读取；
    手动历史转发由会话提供器负责。
    """
    backend = settings.MAF_SESSION_BACKEND.lower()
    if backend not in {"postgres", "file", "memory"}:
        raise ValueError(f"MAF_SESSION_BACKEND must be one of postgres|file|memory, got {backend!r}")
    logger.debug("Session storage backend: %s (not yet wired — returning None)", backend)
    return None


def get_checkpoint_storage(*, pool: Any = None) -> Any:
    """按 MAF_CHECKPOINT_BACKEND 创建工作流检查点存储。

    postgres 写入 workflow_checkpoints，需要传入连接池或从 shared.db
    读取；连接池未初始化时返回 None。file 使用 MAF_CHECKPOINT_DIR，
    memory 用于无需持久化的测试。
    """
    backend = settings.MAF_CHECKPOINT_BACKEND.lower()

    if backend == "file":
        from agent_framework._workflows._checkpoint import FileCheckpointStorage

        Path(settings.MAF_CHECKPOINT_DIR).mkdir(parents=True, exist_ok=True)
        return FileCheckpointStorage(settings.MAF_CHECKPOINT_DIR)

    if backend == "memory":
        from agent_framework._workflows._checkpoint import InMemoryCheckpointStorage

        return InMemoryCheckpointStorage()

    if backend == "postgres":
        if pool is None:
            try:
                from shared.db import get_pool

                pool = get_pool()
            except Exception as exc:  # 连接池未初始化，常见于测试或脚本。
                logger.debug("Postgres pool unavailable for checkpoint storage: %s", exc)
                return None
        from shared.checkpoint_storage import PostgresCheckpointStorage

        return PostgresCheckpointStorage(pool)

    raise ValueError(f"MAF_CHECKPOINT_BACKEND must be one of postgres|file|memory, got {backend!r}")


# ─────────────────────── OAuth2 token verification ─────────


@lru_cache(maxsize=1)
def get_token_verifier() -> Any:
    """oauth 模式返回 RS256 校验器，否则返回 None。

    None 表示继续使用本地 HS256 校验，避免 local 模式也创建
    会立即初始化 PyJWKClient 的 RS256Verifier。
    """
    if settings.AUTH_MODE != "oauth":
        return None
    from shared.oauth.verifier import RS256Verifier

    return RS256Verifier()


# ─────────────────────── Back-compat shims ─────────────────

# 兼容旧代码从 shared.agent_factory 导入的工厂名称。
# 使用同名导出，保持调用方不变，
# 具体实现集中在当前模块。
create_chat_client = get_chat_client
create_embedding_client = get_embeddings_client


class EmbeddingsUnavailableError(ValueError):
    """没有配置可用于当前数据维度的嵌入服务。"""


def get_compatible_client(provider: str) -> OpenAIChatCompletionClient:
    """保留 MAF 原生工具执行层，在 HTTP 边界限制模型与预算。"""
    import httpx

    from shared.paid_transport import PaidTransport

    key = settings.DEEPSEEK_API_KEY if provider == "deepseek" else settings.MOONSHOT_API_KEY
    if not key:
        raise ValueError(f"{provider} API key is required")
    model = "deepseek-flash" if provider == "deepseek" else "kimi-k3"
    endpoint = "https://api.deepseek.com/v1" if provider == "deepseek" else "https://api.moonshot.cn/v1"
    http_client = httpx.AsyncClient(transport=PaidTransport(provider), timeout=60.0, follow_redirects=False)
    client = openai.AsyncOpenAI(api_key=key, base_url=endpoint, max_retries=0, http_client=http_client)
    return OpenAIChatCompletionClient(model=model, async_client=client)
