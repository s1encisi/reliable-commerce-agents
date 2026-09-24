"""角色相关提示词回归测试。

旧实现导入时按 customer 固定系统提示词，管理员和商家也收到客户
指令。工厂现在每次按 current_user_role 组合，测试确认角色变化会
改变提示词；无需模型或数据库。
"""

from __future__ import annotations

import pytest

from shared.config import settings
from shared.context import current_user_role


@pytest.fixture(autouse=True)
def _openai_dummy(monkeypatch: pytest.MonkeyPatch) -> None:
    # 客户端构建只检查密钥非空，不访问网络。
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai", raising=False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key", raising=False)


def _instructions(agent: object) -> str:
    opts = getattr(agent, "default_options", None)
    assert isinstance(opts, dict), f"default_options is {type(opts)!r}, expected dict"
    instructions = opts.get("instructions")
    assert instructions, "agent was built with no instructions"
    return instructions


def test_order_management_agent_uses_seller_prompt_for_seller_role() -> None:
    from order_management.agent import create_order_management_agent

    current_user_role.set("seller")
    instructions = _instructions(create_order_management_agent())

    assert "当商家询问订单时，请展示包含他所售商品的订单" in instructions
    assert "该用户是客户" not in instructions
    assert "管理员，拥有对全部数据和全部智能体的完整访问权限" not in instructions


def test_order_management_agent_uses_admin_prompt_for_admin_role() -> None:
    from order_management.agent import create_order_management_agent

    current_user_role.set("admin")
    instructions = _instructions(create_order_management_agent())

    assert "管理员，拥有对全部数据和全部智能体的完整访问权限" in instructions
    assert "该用户是客户" not in instructions
    assert "当商家询问订单时" not in instructions


def test_order_management_agent_falls_back_to_customer_prompt_when_role_unset() -> None:
    from order_management.agent import create_order_management_agent

    current_user_role.set("")
    instructions = _instructions(create_order_management_agent())

    assert "该用户是客户" in instructions

    current_user_role.set("customer")
