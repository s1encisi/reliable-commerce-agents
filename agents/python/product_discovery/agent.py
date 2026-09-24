"""商品发现（product discovery）智能体定义。

当 ``settings.MCP_ENABLED`` 为 True 时，智能体通过 ``MCPStreamableHTTPTool``
连接到商品 MCP 服务（``ecommerce_mcp_product.server``），而不是直接调用
asyncpg。两种模式对外暴露的能力相同。
"""

from agent_framework import Agent
from agent_framework._mcp import MCPStreamableHTTPTool

from product_discovery.prompts import get_system_prompt
from product_discovery.tools import (
    compare_products,
    find_similar_products,
    get_product_details,
    get_trending_products,
    search_products,
    semantic_search,
)
from shared.agent_factory import create_chat_client
from shared.config import settings
from shared.context import current_user_role
from shared.context_providers import ECommerceContextProvider
from shared.middleware import build_specialist_middleware
from shared.oauth.service_client import acquire_service_token, build_mcp_http_client, set_mcp_auth_header
from shared.tools.inventory_tools import check_stock, get_warehouse_availability
from shared.tools.memory_tools import recall_memories, store_memory
from shared.tools.pricing_tools import get_price_history
from shared.tools.user_tools import get_purchase_history, get_user_profile

# 由 create_product_discovery_agent() 在 MCP_ENABLED+MCP_AUTH_ENABLED 时设置一次；
# refresh_mcp_auth()（由 main.py 中的异步启动钩子调用）在取得令牌后设置其
# Authorization 头 —— 智能体构造本身是同步的，无法等待客户端凭证授予流程。
_mcp_product_http_client = None

AGENT_TOOLS = [
    search_products,
    get_product_details,
    compare_products,
    semantic_search,
    find_similar_products,
    get_trending_products,
    check_stock,
    get_warehouse_availability,
    get_price_history,
    get_user_profile,
    get_purchase_history,
    store_memory,
    recall_memories,
]


def create_product_discovery_agent() -> Agent:
    """创建商品发现 ChatAgent。

    当 ``MCP_ENABLED=true`` 时使用 MCP 服务，否则使用直接的 asyncpg 工具。
    """
    global _mcp_product_http_client
    if settings.MCP_ENABLED:
        # MCP 路径：核心商品工具（含价格历史）来自 MCP 服务。
        # 语义搜索和用户上下文工具仍在本地运行，因为它们依赖 MCP 服务
        # 未暴露的 pgvector / ContextVars —— get_price_history 不在此列
        # （MCP 服务已有自己的版本；以同名注册两者会在智能体构造时
        # 抛出 "Duplicate tool name"）。
        if settings.MCP_AUTH_ENABLED:
            _mcp_product_http_client = build_mcp_http_client()
        mcp_product = MCPStreamableHTTPTool(
            name="product-mcp",
            url=settings.MCP_PRODUCT_SERVER_URL,
            description="Product catalog data via MCP",
            http_client=_mcp_product_http_client,
        )
        tools: list = [
            mcp_product,
            semantic_search,
            find_similar_products,
            check_stock,
            get_user_profile,
            get_purchase_history,
        ]
    else:
        tools = AGENT_TOOLS  # type: ignore[assignment]

    return Agent(
        client=create_chat_client(),
        name="product-discovery",
        description="Natural language product search, semantic similarity, recommendations, and price tracking.",
        instructions=get_system_prompt(current_user_role.get() or "customer"),
        tools=tools,
        context_providers=[ECommerceContextProvider()],
        middleware=build_specialist_middleware(),
    )


async def refresh_mcp_auth() -> None:
    """获取（或刷新）``mcp:product`` 服务令牌，并将其设为共享 MCP http 客户端
    上的默认 Authorization 头。由 ``main.py`` 中的异步启动钩子调用一次 ——
    智能体构造本身是同步的，无法等待客户端凭证授予流程。"""
    if _mcp_product_http_client is None:
        return
    token = await acquire_service_token(settings.MCP_PRODUCT_REQUIRED_SCOPE, settings.MCP_PRODUCT_AUDIENCE)
    set_mcp_auth_header(_mcp_product_http_client, token)
