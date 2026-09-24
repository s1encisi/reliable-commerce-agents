"""编排器（客服）—— FastAPI 入口点。

这是所有客户端请求的主网关。与使用 A2AAgentHost 的专业智能体不同，
编排器是一个完整的 FastAPI 应用，处理认证、聊天、市场与管理端点。

本地运行：
    cd agents && uv run uvicorn orchestrator.main:app --port 8080 --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from asyncpg.exceptions import ForeignKeyViolationError
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from orchestrator.routes import router
from shared.db import close_db_pool, init_db_pool
from shared.telemetry import instrument_fastapi, setup_telemetry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：遥测 + DB 连接池。关闭：关闭连接池。"""
    setup_telemetry("ecommerce.orchestrator")
    instrument_fastapi(app)
    await init_db_pool()
    logger.info("orchestrator.started")
    yield
    await close_db_pool()
    logger.info("orchestrator.stopped")


app = FastAPI(
    title="E-Commerce Agents",
    description="E-Commerce Multi-Agent Platform — Orchestrator API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.exception_handler(ForeignKeyViolationError)
async def stale_user_handler(request: Request, exc: ForeignKeyViolationError) -> JSONResponse:
    # JWT 携带的 user_id 已不在 users 表中 —— 通常发生在数据库重新播种之后。
    # 映射为 401，以便前端清除其令牌。
    detail = str(exc)
    if "user_id" in detail:
        logger.warning("stale_jwt.fk_violation path=%s", request.url.path)
        return JSONResponse(
            status_code=401,
            content={"detail": "Session invalid — please log in again."},
        )
    logger.exception("fk_violation.unhandled path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Database constraint error"},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查端点。"""
    return {"status": "ok", "service": "orchestrator"}
