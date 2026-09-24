"""智能体工厂测试。

用测试占位密钥构建，检查名称、工具数量与中间件组合；
构建阶段不发起网络、模型或数据库调用。
"""

from __future__ import annotations

import importlib

import pytest

from shared.config import settings

# 元组内容：模块、工厂、预期智能体名称。
FACTORIES = [
    ("product_discovery.agent", "create_product_discovery_agent", "product-discovery"),
    ("order_management.agent", "create_order_management_agent", "order-management"),
    ("pricing_promotions.agent", "create_pricing_promotions_agent", "pricing-promotions"),
    ("review_sentiment.agent", "create_review_sentiment_agent", "review-sentiment"),
    ("inventory_fulfillment.agent", "create_inventory_fulfillment_agent", "inventory-fulfillment"),
    ("orchestrator.agent", "create_orchestrator_agent", "orchestrator"),
]


@pytest.fixture(autouse=True)
def _openai_dummy(monkeypatch: pytest.MonkeyPatch) -> None:
    # 创建客户端只检查密钥非空，构建时不访问网络。
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai", raising=False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key", raising=False)


def _tool_list(mod: object) -> list:
    tools = getattr(mod, "AGENT_TOOLS", None)
    if tools is None:
        tools = getattr(mod, "ORCHESTRATOR_TOOLS", None)
    assert tools is not None, "module exposes neither AGENT_TOOLS nor ORCHESTRATOR_TOOLS"
    return list(tools)


def _options(agent: object) -> dict:
    opts = getattr(agent, "default_options", None)
    assert isinstance(opts, dict), f"default_options is {type(opts)!r}, expected dict"
    return opts


@pytest.mark.parametrize("module,factory,expected_name", FACTORIES)
def test_factory_builds_named_agent(module: str, factory: str, expected_name: str) -> None:
    mod = importlib.import_module(module)
    agent = getattr(mod, factory)()
    assert agent.name == expected_name


@pytest.mark.parametrize("module,factory,expected_name", FACTORIES)
def test_factory_attaches_full_tool_set(module: str, factory: str, expected_name: str) -> None:
    mod = importlib.import_module(module)
    agent = getattr(mod, factory)()
    tools = _options(agent).get("tools") or []
    assert len(list(tools)) == len(_tool_list(mod))


@pytest.mark.parametrize("module,factory,expected_name", FACTORIES)
def test_factory_composes_system_prompt(module: str, factory: str, expected_name: str) -> None:
    mod = importlib.import_module(module)
    agent = getattr(mod, factory)()
    assert _options(agent).get("instructions")  # 确认 YAML 组合提示词存在。
