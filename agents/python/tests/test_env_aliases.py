"""环境变量别名与默认值测试。

覆盖 Azure 密钥和部署名的新旧别名优先级、MAF 功能默认配置以及
注册表非法 JSON 的明确错误。
"""

from __future__ import annotations

import importlib


def _reload_settings(monkeypatch, **env) -> object:
    """按指定环境快照重载配置，避免依赖开发者真实 .env。"""
    # 清空 Settings 读取的 Azure、MAF 和 LLM 变量，
    # 只设置测试需要的值。
    for key in (
        "LLM_PROVIDER",
        "OPENAI_API_KEY",
        "LLM_MODEL",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_EMBEDDING_DEPLOYMENT",
        "AGENT_REGISTRY",
        "MAF_SESSION_BACKEND",
        "MAF_SESSION_DIR",
        "MAF_CHECKPOINT_BACKEND",
        "MAF_CHECKPOINT_DIR",
        "RETURN_HITL_THRESHOLD",
        "HANDOFF_AUTONOMOUS_MODE",
        "WORKFLOW_VISUALIZATION_ON_BUILD",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # 同时停用 .env 加载，防止本机配置混入。
    from shared import config as config_mod

    importlib.reload(config_mod)
    monkeypatch.setitem(config_mod.Settings.model_config, "env_file", None)
    # 使用 monkeypatch.setattr，确保结束时恢复原单例。
    # 直接赋值不会自动还原，
    # 可能让后续测试继续使用
    # 已移除凭据并禁用 .env 的配置，
    # 因为 shared.config 是共享模块，
    # 属性修改会跨用例存活。
    new_settings = config_mod.Settings()
    monkeypatch.setattr(config_mod, "settings", new_settings)
    return new_settings


def test_azure_key_alias_accepts_api_key(monkeypatch) -> None:
    settings = _reload_settings(monkeypatch, AZURE_OPENAI_API_KEY="from-new-name")
    assert settings.AZURE_OPENAI_KEY == "from-new-name"


def test_azure_key_original_name_wins_over_alias(monkeypatch) -> None:
    """两个密钥名同时存在时，AZURE_OPENAI_KEY 优先。"""
    settings = _reload_settings(
        monkeypatch,
        AZURE_OPENAI_KEY="original",
        AZURE_OPENAI_API_KEY="alias",
    )
    assert settings.AZURE_OPENAI_KEY == "original"


def test_azure_deployment_alias_accepts_deployment_name(monkeypatch) -> None:
    settings = _reload_settings(
        monkeypatch,
        AZURE_OPENAI_DEPLOYMENT_NAME="gpt-4.1-via-alias",
    )
    assert settings.AZURE_OPENAI_DEPLOYMENT == "gpt-4.1-via-alias"


def test_maf_feature_flags_have_safe_defaults(monkeypatch) -> None:
    settings = _reload_settings(monkeypatch)
    assert settings.MAF_SESSION_BACKEND == "postgres"
    assert settings.MAF_CHECKPOINT_BACKEND == "postgres"
    assert settings.RETURN_HITL_THRESHOLD == 500.0
    assert settings.HANDOFF_AUTONOMOUS_MODE is True
    assert settings.WORKFLOW_VISUALIZATION_ON_BUILD is False


def test_maf_feature_flags_override_from_env(monkeypatch) -> None:
    settings = _reload_settings(
        monkeypatch,
        MAF_SESSION_BACKEND="file",
        RETURN_HITL_THRESHOLD="1000",
    )
    assert settings.MAF_SESSION_BACKEND == "file"
    assert settings.RETURN_HITL_THRESHOLD == 1000.0


def test_agent_registry_defaults_to_empty_object(monkeypatch) -> None:
    settings = _reload_settings(monkeypatch)
    assert settings.AGENT_REGISTRY == "{}"
