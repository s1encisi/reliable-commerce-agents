"""敏感状态变更工具必须声明 always_require 审批。

只读工具不要求审批；购物车变更易撤销，按现有产品约定不增加该门控。
"""

from __future__ import annotations

import pytest

# ─────────────────────── Tool inventory ──────────────────────────


def _required_approval_tools():
    """延迟导入，避免测试收集触发生产密钥校验。"""
    from inventory_fulfillment.tools import place_backorder
    from order_management.tools import cancel_order, modify_order
    from shared.tools.return_tools import initiate_return, process_refund

    return [
        ("cancel_order", cancel_order),
        ("modify_order", modify_order),
        ("initiate_return", initiate_return),
        ("process_refund", process_refund),
        ("place_backorder", place_backorder),
    ]


def _read_only_tools():
    from order_management.tools import get_order_details, get_user_orders
    from shared.tools.return_tools import check_return_eligibility, get_return_status

    return [
        ("get_user_orders", get_user_orders),
        ("get_order_details", get_order_details),
        ("check_return_eligibility", check_return_eligibility),
        ("get_return_status", get_return_status),
    ]


# ─────────────────────── Assertions ───────────────────────────────


@pytest.mark.parametrize("name,tool", _required_approval_tools())
def test_destructive_tool_requires_approval(name: str, tool) -> None:
    assert tool.approval_mode == "always_require", (
        f"{name} must be wired with approval_mode='always_require' so the "
        "front-end / HITL layer can confirm before the LLM cancels orders, "
        "issues refunds, modifies addresses or commits backorders."
    )


@pytest.mark.parametrize("name,tool", _read_only_tools())
def test_read_only_tool_does_not_require_approval(name: str, tool) -> None:
    assert tool.approval_mode == "never_require", (
        f"{name} is a lookup tool; gating it behind approval would make the "
        "chat UX unbearable. If you need to escalate it, do so via the "
        "ECommerceContextProvider, not approval_mode."
    )
