"""工具角色授权测试。

覆盖装饰器、守卫及 @tool 组合后的签名保留，不使用外部服务。
"""

from __future__ import annotations

import inspect

import pytest
from agent_framework import tool

from shared.config import settings
from shared.context import current_user_role
from shared.guardrails.roles import ensure_role, requires_role


@requires_role("seller", "admin")
async def _sample(x: int) -> dict:
    return {"ok": x}


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setattr(settings, "GUARDRAILS_ENABLED", True)


async def test_allows_listed_role() -> None:
    current_user_role.set("seller")
    assert await _sample(1) == {"ok": 1}


async def test_admin_always_allowed() -> None:
    current_user_role.set("admin")
    assert await _sample(2) == {"ok": 2}


async def test_denies_customer() -> None:
    current_user_role.set("customer")
    out = await _sample(3)
    assert out["error"] == "permission_denied"
    assert "seller" in out["required_roles"]


async def test_denies_missing_role() -> None:
    current_user_role.set("")
    out = await _sample(4)
    assert out["error"] == "permission_denied"


async def test_disabled_bypasses(monkeypatch) -> None:
    monkeypatch.setattr(settings, "GUARDRAILS_ENABLED", False)
    current_user_role.set("customer")
    assert await _sample(5) == {"ok": 5}


def test_decorator_preserves_signature() -> None:
    # wraps 保留名称与签名，供 MAF 读取。
    assert _sample.__name__ == "_sample"
    assert "x" in inspect.signature(_sample).parameters


def test_composes_under_tool_decorator() -> None:
    @tool(name="sample_tool", description="A sample tool.")
    @requires_role("seller", "admin")
    async def sample_tool(
        x: int,
    ) -> dict:
        return {"ok": x}

    # 工具对象仍需保留名称及 x 参数模式，
    # 证明角色装饰器没有擦除函数签名。
    assert getattr(sample_tool, "name", None) == "sample_tool"
    blob = repr(getattr(sample_tool, "parameters", sample_tool))
    assert "x" in blob


# 守卫辅助函数


async def test_ensure_role_allows() -> None:
    current_user_role.set("seller")
    assert ensure_role("seller", "admin", tool="t") is None


async def test_ensure_role_denies() -> None:
    current_user_role.set("customer")
    denied = ensure_role("seller", "admin", tool="t")
    assert denied and denied["error"] == "permission_denied"


async def test_ensure_role_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "GUARDRAILS_ENABLED", False)
    current_user_role.set("customer")
    assert ensure_role("seller", "admin", tool="t") is None
