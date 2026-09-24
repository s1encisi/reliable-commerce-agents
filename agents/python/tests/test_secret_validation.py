"""JWT 与智能体共享密钥校验测试，隔离重建 Settings，避免 .env 干扰。"""

from __future__ import annotations

import logging

import pytest

# 设置 production 之前先导入模块，
# 让模块单例在开发默认值下初始化，
# 随后各例自行构造 Settings(_env_file=None)，
# 测试自己的环境组合。
from shared import config as config_mod  # noqa: E402


def _prepare_env(monkeypatch: pytest.MonkeyPatch, *, environment: str, **secrets: str):
    for key in (
        "ENVIRONMENT",
        "JWT_SECRET",
        "AGENT_SHARED_SECRET",
        "OPENAI_API_KEY",
        "LLM_PROVIDER",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("ENVIRONMENT", environment)
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)

    return config_mod


_STRONG_SECRET = "x" * 48  # 48 字节，超过 32 字节下限。
_STRONG_SECOND = "y" * 48


def test_production_rejects_default_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    config_mod = _prepare_env(
        monkeypatch,
        environment="production",
        AGENT_SHARED_SECRET=_STRONG_SECRET,
    )
    with pytest.raises(ValueError, match="JWT_SECRET is unsafe"):
        config_mod.Settings(_env_file=None)


def test_production_rejects_short_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    config_mod = _prepare_env(
        monkeypatch,
        environment="production",
        JWT_SECRET="too-short",
        AGENT_SHARED_SECRET=_STRONG_SECRET,
    )
    with pytest.raises(ValueError, match=r"JWT_SECRET is unsafe \(.*< 32"):
        config_mod.Settings(_env_file=None)


def test_production_rejects_default_agent_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    config_mod = _prepare_env(
        monkeypatch,
        environment="production",
        JWT_SECRET=_STRONG_SECRET,
    )
    with pytest.raises(ValueError, match="AGENT_SHARED_SECRET is unsafe"):
        config_mod.Settings(_env_file=None)


def test_production_accepts_strong_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    config_mod = _prepare_env(
        monkeypatch,
        environment="production",
        JWT_SECRET=_STRONG_SECRET,
        AGENT_SHARED_SECRET=_STRONG_SECOND,
    )
    settings = config_mod.Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.JWT_SECRET == _STRONG_SECRET
    assert settings.AGENT_SHARED_SECRET == _STRONG_SECOND


def test_development_warns_but_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """开发环境仍允许占位值，但应告警。"""
    config_mod = _prepare_env(monkeypatch, environment="development")
    with caplog.at_level(logging.WARNING):
        settings = config_mod.Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.JWT_SECRET  # 成功加载，没有抛错。
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("JWT_SECRET" in m for m in messages)
    assert any("AGENT_SHARED_SECRET" in m for m in messages)


def test_test_environment_also_permits_weak_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """test 环境不能因默认占位值阻止启动。"""
    config_mod = _prepare_env(monkeypatch, environment="test")
    config_mod.Settings(_env_file=None)  # type: ignore[call-arg]  # must not raise
