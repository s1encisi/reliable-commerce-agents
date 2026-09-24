"""库存与履约（inventory & fulfillment）智能体定义。

当 ``settings.MCP_ENABLED`` 为 True 时，智能体通过 ``MCPStreamableHTTPTool``
连接到库存 MCP 服务（``ecommerce_mcp_inventory.server``），而不是直接调用
asyncpg。两种模式对外暴露的能力相同。
"""

from agent_framework import Agent
from agent_framework._mcp import MCPStreamableHTTPTool

from inventory_fulfillment.prompts import get_system_prompt
from inventory_fulfillment.tools import (
    calculate_fulfillment_plan,
    compare_carriers,
    estimate_shipping,
    get_restock_schedule,
    get_tracking_status,
    place_backorder,
)
from shared.agent_factory import create_chat_client
from shared.config import settings
from shared.context import current_user_role
from shared.context_providers import ECommerceContextProvider
from shared.middleware import build_specialist_middleware
from shared.oauth.service_client import acquire_service_token, build_mcp_http_client, set_mcp_auth_header
from shared.tools.inventory_tools import check_stock, get_warehouse_availability
from shared.tools.product_lookup_tools import find_product_by_name
from shared.tools.user_tools import get_user_profile

AGENT_TOOLS = [
    find_product_by_name,
    check_stock,
    get_warehouse_availability,
    get_restock_schedule,
    estimate_shipping,
    compare_carriers,
    get_tracking_status,
    calculate_fulfillment_plan,
    place_backorder,
    get_user_profile,
]

# 由 create_inventory_fulfillment_agent() 在 MCP_ENABLED+MCP_AUTH_ENABLED 时设置一次；
# refresh_mcp_auth()（由 main.py 中的异步启动钩子调用）在取得令牌后设置其
# Authorization 头 —— 智能体构造本身是同步的，无法等待客户端凭证授予流程。
_mcp_inventory_http_client = None


def create_inventory_fulfillment_agent() -> Agent:
    """创建库存与履约 ChatAgent。

    当 ``MCP_ENABLED=true`` 时使用 MCP 服务，否则使用直接的 asyncpg 工具。
    """
    global _mcp_inventory_http_client
    if settings.MCP_ENABLED:
        # MCP 路径：工具在启动时从运行中的 MCP 服务发现。
        # 非 MCP 工具（追踪、履约计划、缺货预订、user_profile）仍在本地
        # 运行，因为它们尚未通过 MCP 服务暴露。
        if settings.MCP_AUTH_ENABLED:
            _mcp_inventory_http_client = build_mcp_http_client()
        mcp_inventory = MCPStreamableHTTPTool(
            name="inventory-mcp",
            url=settings.MCP_INVENTORY_SERVER_URL,
            description="Inventory and fulfillment data via MCP",
            http_client=_mcp_inventory_http_client,
        )
        tools: list = [
            mcp_inventory,
            find_product_by_name,
            get_tracking_status,
            calculate_fulfillment_plan,
            place_backorder,
            get_user_profile,
        ]
    else:
        tools = AGENT_TOOLS  # type: ignore[assignment]

    return Agent(
        client=create_chat_client(),
        name="inventory-fulfillment",
        description="Real-time inventory tracking, shipping estimation, carrier comparison, and backorder management.",
        instructions=get_system_prompt(current_user_role.get() or "customer"),
        tools=tools,
        context_providers=[ECommerceContextProvider()],
        middleware=build_specialist_middleware(),
    )


async def refresh_mcp_auth() -> None:
    """获取（或刷新）``mcp:inventory`` 服务令牌，并将其设为共享 MCP http 客户端
    上的默认 Authorization 头。由 ``main.py`` 中的异步启动钩子调用一次 ——
    智能体构造本身是同步的，无法等待客户端凭证授予流程。"""
    if _mcp_inventory_http_client is None:
        return
    token = await acquire_service_token(settings.MCP_INVENTORY_REQUIRED_SCOPE, settings.MCP_INVENTORY_AUDIENCE)
    set_mcp_auth_header(_mcp_inventory_http_client, token)
