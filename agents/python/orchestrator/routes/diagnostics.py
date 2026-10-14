"""公开非敏感运行能力，仅用于已认证客户端选择正确演示路径。"""

from fastapi import APIRouter, Depends

from orchestrator.routes.legacy import require_auth
from shared.config import settings

router = APIRouter()


@router.get("/api/runtime/capabilities")
async def runtime_capabilities(user: dict = Depends(require_auth)) -> dict:
    offline = settings.LLM_PROVIDER == "replay" and not settings.RECORD and settings.DECISION_ROUTING_MODE == "off"
    return {
        "offline_demo": offline,
        "task_demo_goal": "筛选两款商品并核对库存" if offline else None,
        "context_auto_enabled": settings.DECISION_CONTEXT_ENABLED,
        "context_explicit_edit": True,
        "fulfillment_simulation": settings.FULFILLMENT_SIMULATION_ENABLED,
        "payment_integration": False,
        "procurement": {
            "enabled": settings.PROCUREMENT_ENABLED
            and not settings.MCP_ENABLED
            and (
                not settings.PROCUREMENT_ALLOWLIST.strip()
                or user.get("sub", "") in {v.strip() for v in settings.PROCUREMENT_ALLOWLIST.split(",")}
            ),
            "mode": settings.PROCUREMENT_MODE,
            "model_authorized": settings.PROCUREMENT_MODEL_AUTHORIZED,
            "quote_policy": "single_coupon_v1",
            "currency": "CNY",
            "write_operations": False,
            "demo_goal": "采购3副采购专用耳机，货款预算1500元，需要两个独立备选方案。",
        },
    }
