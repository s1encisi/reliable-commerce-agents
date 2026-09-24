"""注入检测提供方配置测试。

尚未实现的 azure_content_safety 必须在启动时失败，不能静默使用
regex。隔离创建 Settings，避免仓库 .env 干扰。
"""

from __future__ import annotations

import pytest

from shared import config as config_mod


def _prepare_env(monkeypatch: pytest.MonkeyPatch, *, provider: str | None, environment: str = "test"):
    for key in ("ENVIRONMENT", "JWT_SECRET", "AGENT_SHARED_SECRET", "GUARDRAILS_INJECTION_PROVIDER"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("ENVIRONMENT", environment)
    if provider is not None:
        monkeypatch.setenv("GUARDRAILS_INJECTION_PROVIDER", provider)

    return config_mod


def test_default_provider_is_regex_and_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _prepare_env(monkeypatch, provider=None)
    settings = mod.Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.GUARDRAILS_INJECTION_PROVIDER == "regex"


def test_explicit_regex_provider_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _prepare_env(monkeypatch, provider="regex")
    settings = mod.Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.GUARDRAILS_INJECTION_PROVIDER == "regex"


def test_azure_content_safety_rejected_as_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _prepare_env(monkeypatch, provider="azure_content_safety")
    with pytest.raises(ValueError, match="not implemented"):
        mod.Settings(_env_file=None)  # type: ignore[call-arg]


def test_azure_content_safety_rejected_even_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    """此校验与密钥强度警告不同，不随环境降级为警告。"""
    mod = _prepare_env(monkeypatch, provider="azure_content_safety", environment="development")
    with pytest.raises(ValueError, match="not implemented"):
        mod.Settings(_env_file=None)  # type: ignore[call-arg]


def test_unknown_provider_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _prepare_env(monkeypatch, provider="some-made-up-provider")
    with pytest.raises(ValueError, match="not a recognized value"):
        mod.Settings(_env_file=None)  # type: ignore[call-arg]
