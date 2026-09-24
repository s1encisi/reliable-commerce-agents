"""编排器路由包 —— 组合聊天、编排以及其余一切。

``router`` 是 ``orchestrator/main.py`` 所包含的唯一 ``APIRouter``；
导入 ``from orchestrator.routes import router``（或认证依赖 / ``settings``）
的调用方，看到的行为与 Phase 1.3 拆分之前完全一致 —— 本包的存在是为了把
聊天（每种编排模式都会触及）与那些不随模式增加而改变的路由分开，
而不是改变任何对外契约。
"""

from __future__ import annotations

from fastapi import APIRouter

from shared.config import settings

from . import chat, legacy, orchestration, tasks
from .legacy import optional_auth, require_admin, require_auth, require_seller

router = APIRouter()
router.include_router(legacy.router)
router.include_router(chat.router)
router.include_router(orchestration.router)
router.include_router(tasks.router)

__all__ = [
    "router",
    "settings",
    "optional_auth",
    "require_auth",
    "require_admin",
    "require_seller",
]
