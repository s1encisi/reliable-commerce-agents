"""提示词组合的纯字符串测试。

核对每个智能体保留共享安全规则、数据事实约束及角色专属说明，
不调用模型或数据库。
"""

from __future__ import annotations

import pytest

from shared.prompt_loader import load_prompt

AGENTS = [
    "product-discovery",
    "order-management",
    "pricing-promotions",
    "review-sentiment",
    "inventory-fulfillment",
    "orchestrator",
]

SECURITY_HEADERS = [
    "Prompt-Injection Resistance",
    "Role Confinement",
    "Refusal Policy",
]


@pytest.mark.parametrize("agent", AGENTS)
def test_prompt_includes_security_guardrails(agent: str) -> None:
    prompt = load_prompt(agent, "customer")
    assert prompt, f"{agent} produced an empty prompt"
    for header in SECURITY_HEADERS:
        assert header in prompt, f"{agent} prompt missing '{header}' section"


@pytest.mark.parametrize("agent", AGENTS)
def test_prompt_keeps_data_grounding(agent: str) -> None:
    prompt = load_prompt(agent, "customer").lower()
    assert "data grounding rules" in prompt
    assert "hallucinate" in prompt  # 核心禁止编造指令仍存在。


def test_injection_rules_name_the_canonical_attacks() -> None:
    prompt = load_prompt("orchestrator", "customer").lower()
    # 检查实际护栏措辞，不只检查标题。
    assert "as data, never as instructions" in prompt
    assert "ignore previous instructions" in prompt
    assert "never reveal" in prompt and "system prompt" in prompt


def test_role_confinement_rejects_self_claimed_privilege() -> None:
    prompt = load_prompt("order-management", "customer").lower()
    assert "fixed by the authenticated user's role" in prompt
    assert "i am an admin" in prompt  # 明确包含越权尝试的说明。


def test_prompt_is_role_aware() -> None:
    # 编排器应区分客户与管理员指令。
    customer = load_prompt("orchestrator", "customer")
    admin = load_prompt("orchestrator", "admin")
    assert customer and admin
    assert customer != admin, "orchestrator prompt did not vary by role"


def test_unknown_role_falls_back_but_keeps_guardrails() -> None:
    # 未知角色仍必须收到共享安全规则。
    prompt = load_prompt("orchestrator", "no-such-role")
    assert prompt
    for header in SECURITY_HEADERS:
        assert header in prompt


def test_unknown_agent_returns_empty() -> None:
    assert load_prompt("does-not-exist", "customer") == ""
