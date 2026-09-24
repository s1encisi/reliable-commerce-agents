# 可靠电商多智能体平台的多目标镜像。
# 构建上下文为 ./agents；用 AGENT_NAME 选择专业智能体。

FROM python:3.12-slim AS base

# 安装 asyncpg 和 bcrypt 所需系统依赖。
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

# 安装 uv。
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 创建非 root 用户。
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent

WORKDIR /app

# 按提交的锁文件安装依赖，复用镜像缓存层。
# --frozen 避免重新解析依赖时受到外部版本变动影响。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 按需修复早期 MAF 空 __init__.py 的公开导出。
COPY patch_maf.py .
RUN uv run --no-project python patch_maf.py

# 复制所有智能体共用的库与提示词配置。
COPY shared/ shared/
COPY config/ config/

# 复制工作流定义。模式在处理请求时延迟导入这些模块，
# 仅通过启动或导入冒烟检查不足以发现文件缺失。
COPY workflows/ workflows/

# 智能体专属构建参数。
ARG AGENT_NAME=orchestrator
ARG AGENT_PORT=8080

# 复制智能体模块，Python 包名使用下划线。
COPY ${AGENT_NAME}/ ${AGENT_NAME}/

# 编排器的固定工作流直接调用这些专业工具，因此镜像必须包含对应包。
# 延迟导入意味着遗漏只会在选择相关模式时暴露。
# 所有镜像统一复制以保持构建简单；调用边界的进一步拆分属于独立架构改造。
COPY product_discovery/ product_discovery/
COPY order_management/ order_management/
COPY inventory_fulfillment/ inventory_fulfillment/
COPY review_sentiment/ review_sentiment/
COPY pricing_promotions/ pricing_promotions/

# 将应用及运行缓存目录交给非 root 用户。
RUN chown -R agent:agent /app

ENV AGENT_NAME=${AGENT_NAME}
ENV AGENT_PORT=${AGENT_PORT}
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV UV_CACHE_DIR=/app/.cache/uv

# 切换到非 root 用户。
USER agent

EXPOSE ${AGENT_PORT}

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${AGENT_PORT}/health || exit 1

# 使用 Shell 形式在运行时展开环境变量。
# --no-project 跳过项目构建，直接运行模块。
CMD uv run --no-project uvicorn ${AGENT_NAME}.main:app --host 0.0.0.0 --port ${AGENT_PORT}
