"""编排器（客服）—— FastAPI 入口点。

这是所有客户端请求的主网关。与使用 A2AAgentHost 的专业智能体不同，
编排器是一个完整的 FastAPI 应用，处理认证、聊天、市场与管理端点。

本地运行：
    cd agents && uv run uvicorn orchestrator.main:app --port 8080 --reload
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from asyncpg.exceptions import ForeignKeyViolationError
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from orchestrator.routes import router
from shared.db import close_db_pool, init_db_pool
from shared.task_observability import TaskObservabilityMiddleware
from shared.telemetry import instrument_fastapi, setup_telemetry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：遥测 + DB 连接池。关闭：关闭连接池。"""
    setup_telemetry("ecommerce.orchestrator")
    await init_db_pool()
    from shared.config import settings
    from shared.db import get_pool

    stop = asyncio.Event()
    worker = None
    if settings.PROCUREMENT_ENABLED:
        from shared.procurement.runtime import make_runner

        worker = asyncio.create_task(make_runner(get_pool()).serve(stop), name="procurement-worker")
    app.state.procurement_worker = worker
    logger.info("orchestrator.started")
    try:
        yield
    finally:
        stop.set()
        if worker is not None:
            worker.cancel()
            results = await asyncio.gather(worker, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error("procurement.worker_stopped_with_error error=%s", type(result).__name__)
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

app.add_middleware(TaskObservabilityMiddleware)
app.include_router(router)

# ASGI 中间件链会在 lifespan 前构建；SDK 提供器仍在启动时初始化。
instrument_fastapi(app)


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
    worker = getattr(app.state, "procurement_worker", None)
    if worker is not None and worker.done():
        return JSONResponse(
            {"status": "error", "service": "orchestrator", "reason": "procurement_worker_stopped"}, status_code=503
        )
    return {"status": "ok", "service": "orchestrator"}
