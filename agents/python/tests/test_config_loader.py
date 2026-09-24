"""统一工厂测试：提供方分支、缺配置失败、嵌入客户端、
注册表 JSON 与端点校验、缓存以及检查点后端选择。
"""

from __future__ import annotations

import importlib

import pytest


def _reload_with_env(monkeypatch, **env) -> object:
    """按指定环境快照重新加载配置和工厂。"""
    for key in (
        "LLM_PROVIDER",
        "OPENAI_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_KEY",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_VERSION",
        "AGENT_REGISTRY",
        "MAF_CHECKPOINT_BACKEND",
        "MAF_CHECKPOINT_DIR",
        "MAF_SESSION_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    from shared import config as config_mod
    from shared import factory as factory_mod

    importlib.reload(config_mod)
    config_mod.Settings.model_config["env_file"] = None
    config_mod.settings = config_mod.Settings()
    importlib.reload(factory_mod)
    # 每例之间清空注册表 LRU 缓存。
    factory_mod.get_agent_registry.cache_clear()
    return factory_mod


def test_get_chat_client_uses_openai_when_provider_is_openai(monkeypatch) -> None:
    factory = _reload_with_env(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-test",
        LLM_MODEL="gpt-4.1",
    )
    client = factory.get_chat_client()
    from agent_framework.openai import OpenAIChatClient

    assert isinstance(client, OpenAIChatClient)


def test_get_chat_client_honors_llm_base_url_override(monkeypatch) -> None:
    """LLM_BASE_URL 是 OpenAI 兼容端点的统一覆盖机制，
    无需为每个提供方增加专用分支。"""
    factory = _reload_with_env(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="ollama",
        LLM_MODEL="llama3.1:8b",
        LLM_BASE_URL="http://localhost:11434/v1",
    )
    client = factory.get_chat_client()
    from agent_framework.openai import OpenAIChatClient

    assert isinstance(client, OpenAIChatClient)
    assert client.base_url == "http://localhost:11434/v1"


def test_get_chat_client_defaults_to_no_base_url_override(monkeypatch) -> None:
    """未设置 LLM_BASE_URL 时必须使用 SDK 默认地址，不能传入空主机。"""
    factory = _reload_with_env(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-test",
        LLM_MODEL="gpt-4.1",
    )
    client = factory.get_chat_client()
    assert not client.base_url


def test_get_chat_client_uses_azure_when_provider_is_azure(monkeypatch) -> None:
    factory = _reload_with_env(
        monkeypatch,
        LLM_PROVIDER="azure",
        AZURE_OPENAI_ENDPOINT="https://test.openai.azure.com/",
        AZURE_OPENAI_KEY="test",
        AZURE_OPENAI_DEPLOYMENT="gpt-4.1",
    )
    client = factory.get_chat_client()
    assert client is not None


def test_get_chat_client_fails_fast_when_openai_key_missing(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, LLM_PROVIDER="openai")  # 缺少密钥。
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required"):
        factory.get_chat_client()


def test_get_chat_client_fails_fast_when_azure_partial(monkeypatch) -> None:
    factory = _reload_with_env(
        monkeypatch,
        LLM_PROVIDER="azure",
        AZURE_OPENAI_ENDPOINT="https://test.openai.azure.com/",
        # 有意不设置密钥和部署名。
    )
    with pytest.raises(ValueError, match="Azure OpenAI requires"):
        factory.get_chat_client()


def test_get_chat_client_rejects_unknown_provider(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, LLM_PROVIDER="bedrock")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        factory.get_chat_client()


def test_get_agent_registry_parses_json(monkeypatch) -> None:
    factory = _reload_with_env(
        monkeypatch,
        AGENT_REGISTRY='{"a": "http://a:1", "b": "http://b:2"}',
    )
    assert factory.get_agent_registry() == {"a": "http://a:1", "b": "http://b:2"}


def test_get_agent_registry_empty_when_unset(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch)
    assert factory.get_agent_registry() == {}


def test_get_agent_registry_raises_on_malformed_json(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, AGENT_REGISTRY="{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        factory.get_agent_registry()


def test_get_agent_registry_raises_when_not_an_object(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, AGENT_REGISTRY='["list", "not", "dict"]')
    with pytest.raises(ValueError, match="must decode to an object"):
        factory.get_agent_registry()


# 注册表端点校验
#
# 基础设施输出可能尚未解析，
# 得到空字符串，或得到不带协议的主机端口。
# 这些值必须立即拒绝，
# 不能等请求路由时才失败，
# 把部署错误伪装成智能体路由问题。


def test_parse_agent_registry_accepts_a_managed_endpoint_without_a_port() -> None:
    from shared.factory import parse_agent_registry

    registry = parse_agent_registry('{"review-sentiment": "https://rs.internal.azurecontainerapps.io"}')
    assert registry == {"review-sentiment": "https://rs.internal.azurecontainerapps.io"}


def test_parse_agent_registry_is_empty_for_none_and_blank() -> None:
    from shared.factory import parse_agent_registry

    assert parse_agent_registry(None) == {}
    assert parse_agent_registry("") == {}
    assert parse_agent_registry("{}") == {}


def test_parse_agent_registry_rejects_an_empty_url() -> None:
    from shared.factory import parse_agent_registry

    with pytest.raises(ValueError, match="empty URL"):
        parse_agent_registry('{"product-discovery": ""}')


def test_parse_agent_registry_rejects_a_url_with_no_scheme() -> None:
    from shared.factory import parse_agent_registry

    with pytest.raises(ValueError, match="absolute http\\(s\\) URL"):
        parse_agent_registry('{"product-discovery": "product-discovery:8081"}')


def test_parse_agent_registry_names_the_offending_agent() -> None:
    from shared.factory import parse_agent_registry

    with pytest.raises(ValueError, match="order-management"):
        parse_agent_registry('{"product-discovery": "http://pd:8081", "order-management": ""}')


def test_get_checkpoint_storage_file_backend(monkeypatch, tmp_path) -> None:
    factory = _reload_with_env(
        monkeypatch,
        MAF_CHECKPOINT_BACKEND="file",
        MAF_CHECKPOINT_DIR=str(tmp_path / "checkpoints"),
    )
    storage = factory.get_checkpoint_storage()
    from agent_framework._workflows._checkpoint import FileCheckpointStorage

    assert isinstance(storage, FileCheckpointStorage)
    assert (tmp_path / "checkpoints").is_dir()


def test_get_checkpoint_storage_memory_backend(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, MAF_CHECKPOINT_BACKEND="memory")
    storage = factory.get_checkpoint_storage()
    from agent_framework._workflows._checkpoint import InMemoryCheckpointStorage

    assert isinstance(storage, InMemoryCheckpointStorage)


def test_get_checkpoint_storage_rejects_unknown_backend(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, MAF_CHECKPOINT_BACKEND="cassandra")
    with pytest.raises(ValueError, match="postgres\\|file\\|memory"):
        factory.get_checkpoint_storage()


def test_get_session_storage_rejects_unknown_backend(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, MAF_SESSION_BACKEND="cassandra")
    with pytest.raises(ValueError, match="postgres\\|file\\|memory"):
        factory.get_session_storage()


def test_back_compat_symbols_still_available() -> None:
    """兼容入口必须继续导出旧代码使用的两个客户端工厂。"""
    from shared.agent_factory import create_chat_client, create_embedding_client

    assert callable(create_chat_client)
    assert callable(create_embedding_client)
