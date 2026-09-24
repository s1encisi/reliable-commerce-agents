"""智能体认证中间件。

支持两种请求：
1. 智能体间请求：local 模式使用 X-Agent-Secret 中的 AGENT_SHARED_SECRET；
   oauth 模式使用授权服务器签发的 RS256 服务令牌，并直接拒绝共享密钥。
   令牌受众为 ecommerce-agents，范围为 agent:invoke。两种模式均通过
   X-User-Email、X-User-Role、X-Session-Id 转发用户身份；无用户的内部
   调用默认使用 system 角色。
2. 用户请求：local 模式支持 Authorization 中的 JWT Bearer 令牌。
   当前架构下，oauth 用户令牌由编排器路由单独校验，不直接传给专业智能体。
"""

from __future__ import annotations

import logging

import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from shared.config import settings
from shared.context import current_session_id, current_user_email, current_user_role
from shared.jwt_utils import decode_token

logger = logging.getLogger(__name__)

# 跳过认证的路径。
PUBLIC_PATHS = {"/health", "/.well-known/agent-card.json"}

# 平台认可的角色；system 表示没有终端用户的
# 内部或健康检查调用。
_ALLOWED_ROLES = {"customer", "seller", "admin", "system"}


def _identity_anomaly(email: str, role: str) -> str | None:
    """转发身份疑似伪造时返回原因，否则返回 None。

    服务凭据只证明调用方是另一智能体，不能自动证明 x-user-email 与
    x-user-role 正确。识别明显非法值并记录日志；严格身份模式下拒绝请求，
    降低凭据泄露后通过伪造用户头获得访问权的风险。
    """
    if role.lower() not in _ALLOWED_ROLES:
        return f"unknown_role:{role}"
    if email != "system" and "@" not in email:
        return "malformed_email"
    return None


def _apply_forwarded_identity(request: Request, agent_name: str) -> str | None:
    """读取转发身份和会话头，检查伪造风险并设置 ContextVar。

    共享密钥与 OAuth 服务令牌两条认证路径共用本函数。严格身份模式
    需要拒绝时返回原因，否则返回 None。
    """
    email = request.headers.get("x-user-email", "system")
    role = request.headers.get("x-user-role", "system")
    session_id = request.headers.get("x-session-id", "")

    anomaly = _identity_anomaly(email, role)
    if anomaly:
        logger.warning(
            "security.identity_spoof_suspected agent=%s reason=%s email=%s role=%s",
            agent_name,
            anomaly,
            email,
            role,
        )
        if settings.GUARDRAILS_STRICT_IDENTITY:
            return "Invalid forwarded identity"

    from uuid import UUID

    from shared.after_sales.operations import current_operation_id

    operation_id = request.headers.get("X-Return-Operation-Id")
    try:
        current_operation_id.set(str(UUID(operation_id)) if operation_id else None)
    except ValueError:
        return "Invalid return operation identifier"
    from shared.execution_policy import current_execution_policy
    from shared.paid_transport import current_root_run, current_run_deadline

    policy = request.headers.get("x-execution-policy", "normal")
    if policy not in {"normal", "read_only"}:
        return "Invalid execution policy"
    import math
    import time

    try:
        deadline = float(request.headers.get("x-run-deadline", str(time.time() + settings.MAF_STREAM_TIMEOUT_SECONDS)))
    except ValueError:
        return "Invalid run deadline"
    if not math.isfinite(deadline):
        return "Invalid run deadline"
    current_run_deadline.set(min(deadline, time.time() + settings.MAF_STREAM_TIMEOUT_SECONDS))
    current_execution_policy.set(policy)
    current_root_run.set(request.headers.get("x-root-run-id", "")[:64])
    current_user_email.set(email)
    current_user_role.set(role)
    current_session_id.set(session_id)
    return None


class AgentAuthMiddleware(BaseHTTPMiddleware):
    """通过智能体间凭据或 JWT 认证请求。"""

    def __init__(self, app, agent_name: str = "unknown"):
        super().__init__(app)
        self.agent_name = agent_name

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        from shared.execution_policy import current_execution_policy
        from shared.paid_transport import current_root_run, current_run_deadline

        current_execution_policy.set("normal")
        current_root_run.set("")
        current_run_deadline.set(None)
        path = request.url.path

        # 健康检查与智能体名片端点跳过认证。
        if path in PUBLIC_PATHS:
            return await call_next(request)

        agent_secret = request.headers.get("x-agent-secret")

        # oauth 模式完全停用共享密钥。
        # 携带共享密钥的请求直接拒绝，
        # 不能静默继续尝试服务令牌认证。
        if settings.AUTH_MODE == "oauth" and agent_secret:
            logger.warning("auth.denied agent=%s reason=agent_secret_disabled_in_oauth_mode", self.agent_name)
            return JSONResponse({"error": "Inter-agent shared secret is disabled in oauth mode"}, status_code=401)

        # local 模式的智能体间认证：静态共享密钥。
        if agent_secret:
            if agent_secret != settings.AGENT_SHARED_SECRET:
                logger.warning("auth.denied agent=%s reason=invalid_agent_secret", self.agent_name)
                return JSONResponse({"error": "Invalid agent secret"}, status_code=401)

            rejection = _apply_forwarded_identity(request, self.agent_name)
            if rejection:
                return JSONResponse({"error": rejection}, status_code=401)

            logger.info(
                "auth.agent agent=%s user=%s role=%s",
                self.agent_name,
                current_user_email.get(),
                current_user_role.get(),
            )
            return await call_next(request)

        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            logger.warning("auth.denied agent=%s reason=missing_token", self.agent_name)
            return JSONResponse({"error": "Missing or invalid Authorization header"}, status_code=401)

        token = auth_header.removeprefix("Bearer ")

        if settings.AUTH_MODE == "oauth":
            # oauth 模式的智能体间认证：
            # 授权服务器签发的服务令牌证明调用方身份。
            # 实际用户身份仍通过
            # x-user-* 请求头转发，与共享密钥路径一致。
            from shared.factory import get_token_verifier

            try:
                get_token_verifier().decode(token, audience=settings.AUTH_AGENT_AUDIENCE, required_scope="agent:invoke")
            except jwt.ExpiredSignatureError:
                logger.warning("auth.denied agent=%s reason=expired_service_token", self.agent_name)
                return JSONResponse({"error": "Service token expired"}, status_code=401)
            except jwt.PyJWTError:
                logger.warning("auth.denied agent=%s reason=invalid_service_token", self.agent_name)
                return JSONResponse({"error": "Invalid service token"}, status_code=401)

            rejection = _apply_forwarded_identity(request, self.agent_name)
            if rejection:
                return JSONResponse({"error": rejection}, status_code=401)

            logger.info(
                "auth.agent agent=%s user=%s role=%s",
                self.agent_name,
                current_user_email.get(),
                current_user_role.get(),
            )
            return await call_next(request)

        # 用户 JWT 认证，仅用于 local 模式。
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            logger.warning("auth.denied agent=%s reason=expired_token", self.agent_name)
            return JSONResponse({"error": "Token expired"}, status_code=401)
        except jwt.InvalidTokenError:
            logger.warning("auth.denied agent=%s reason=invalid_token", self.agent_name)
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        if payload.get("type") != "access":
            return JSONResponse({"error": "Invalid token type"}, status_code=401)

        email = payload.get("sub", "")
        role = payload.get("role", "customer")

        current_user_email.set(email)
        current_user_role.set(role)
        current_session_id.set(request.headers.get("x-session-id", ""))

        logger.info("auth.user agent=%s user=%s role=%s", self.agent_name, email, role)
        return await call_next(request)
