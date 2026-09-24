"""可靠电商多智能体平台的 OpenTelemetry 初始化。

智能体在生命周期启动时调用 setup_telemetry。HTTP、数据库、模型和
A2A 追踪可发送至 Jaeger；指标与日志虽配置了 OTLP 导出，但需要
支持相应信号的接收端，不能假定 Jaeger 提供指标或结构化日志存储。

自动插桩覆盖 httpx、asyncpg、FastAPI/Starlette 与 Python logging，
并通过 trace_id/span_id 关联请求。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from typing import Any

from shared.config import settings

logger = logging.getLogger(__name__)

_initialized = False


def setup_telemetry(service_name: str, service_version: str = "1.0.0") -> None:
    """配置 OTel 追踪、指标和日志提供器及 OTLP 导出器。

    在处理请求前初始化一次；遥测关闭或初始化失败不应阻断业务。
    """
    global _initialized
    if _initialized:
        return

    if not settings.OTEL_ENABLED:
        logger.info("OpenTelemetry disabled (OTEL_ENABLED=false)")
        _initialized = True
        return

    try:
        _do_setup(service_name, service_version)
        _initialized = True
        logger.info("OpenTelemetry initialized for %s", service_name)
    except Exception:
        logger.exception("Failed to initialize OpenTelemetry — continuing without telemetry")
        _initialized = True


def _do_setup(service_name: str, service_version: str) -> None:
    import os

    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # 启用实验性 GenAI 语义约定，为模型与智能体跨度添加标准属性。
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
    if settings.GENAI_CAPTURE_CONTENT:
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")

    endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT.rstrip("/")

    resource = Resource.create(
        {
            SERVICE_NAME: service_name,
            SERVICE_VERSION: service_version,
            "deployment.environment": settings.ENVIRONMENT,
        }
    )

    # 优先使用 gRPC 导出器；模块不可用时使用 HTTP 导出器。
    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        metric_exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
        logger.info("Using gRPC OTLP exporters → %s", endpoint)
    except ImportError:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        span_exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        metric_exporter = OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics")
        logger.info("Using HTTP OTLP exporters → %s", endpoint)

    # 追踪主接收端，默认使用 Jaeger。
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

    # 可选的并行追踪接收端：Langfuse。
    _maybe_add_langfuse(tracer_provider, BatchSpanProcessor)

    trace.set_tracer_provider(tracer_provider)

    # 指标每 5 秒导出；接收端必须支持 OTLP 指标，Jaeger 不提供该存储。
    metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=5000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # 把 Python 日志桥接为 OTel 日志；需要支持日志的 OTLP 接收端。
    _setup_log_provider(resource, endpoint)

    # 为依赖库安装自动插桩。
    _instrument_openai()
    _instrument_httpx()
    _instrument_asyncpg()
    _instrument_logging()


def _maybe_add_langfuse(tracer_provider: Any, batch_span_processor: Any) -> None:
    """启用 Langfuse 时追加 OTLP 跨度处理器。

    复用标准 HTTP 导出器，无需额外 SDK；失败只记录，不影响主追踪接收端。
    """
    if not settings.LANGFUSE_ENABLED:
        return
    if not (settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
        logger.warning("LANGFUSE_ENABLED=true but LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY is empty — skipping")
        return

    import base64

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as OTLPHttpSpanExporter,
        )

        token = base64.b64encode(f"{settings.LANGFUSE_PUBLIC_KEY}:{settings.LANGFUSE_SECRET_KEY}".encode()).decode()
        host = settings.LANGFUSE_HOST.rstrip("/")
        langfuse_exporter = OTLPHttpSpanExporter(
            endpoint=f"{host}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {token}"},
        )
        tracer_provider.add_span_processor(batch_span_processor(langfuse_exporter))
        logger.info("Langfuse OTel sink enabled → %s", host)
    except Exception:
        logger.exception("Failed to add Langfuse span exporter — continuing without it")


def instrument_fastapi(app: Any) -> None:
    """在 setup_telemetry 之后为 FastAPI 应用安装自动插桩。"""
    if not settings.OTEL_ENABLED:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        logger.exception("Failed to instrument FastAPI")


def instrument_starlette(app: Any) -> None:
    """在 setup_telemetry 之后为 Starlette 智能体宿主安装自动插桩。"""
    if not settings.OTEL_ENABLED:
        return
    try:
        from opentelemetry.instrumentation.starlette import StarletteInstrumentor

        StarletteInstrumentor.instrument_app(app)
    except Exception:
        logger.exception("Failed to instrument Starlette")


def get_tracer(name: str = "ecommerce") -> Any:
    """获取用于创建自定义跨度的 OTel Tracer。"""
    from opentelemetry import trace

    return trace.get_tracer(name)


def get_meter(name: str = "ecommerce") -> Any:
    """获取用于创建自定义指标的 OTel Meter。"""
    from opentelemetry import metrics

    return metrics.get_meter(name)


def get_current_trace_id() -> str | None:
    """返回当前追踪标识的十六进制字符串；没有有效跨度时返回 None。"""
    from opentelemetry import trace

    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return None


def enrich_span_with_session(agent_name: str = "") -> None:
    """将 ContextVar 中的会话、用户和智能体信息加入当前跨度。

    同时设置 session.id 与 gen_ai.conversation.id，便于按会话关联模型调用。
    """
    if not settings.OTEL_ENABLED:
        return
    try:
        from opentelemetry import trace

        from shared.context import current_session_id, current_user_email, current_user_role

        span = trace.get_current_span()
        if not span.is_recording():
            return
        if email := current_user_email.get(""):
            span.set_attribute("enduser.id", email)
        if role := current_user_role.get(""):
            span.set_attribute("enduser.role", role)
        if session := current_session_id.get(""):
            span.set_attribute("session.id", session)
            # gen_ai.conversation.id 是会话关联的 GenAI 语义属性，
            # 用于识别同一会话中的多次模型调用。
            span.set_attribute("gen_ai.conversation.id", session)
        if agent_name:
            span.set_attribute("gen_ai.agent.name", agent_name)
        from shared.paid_transport import current_root_run

        if current_root_run.get():
            span.set_attribute("commerce.run.id", current_root_run.get())
    except Exception:
        pass  # 遥测失败不能中断应用流程。


@contextmanager
def agent_run_span(agent_name: str):
    """为一次智能体调用创建带 GenAI 语义属性的跨度。

    使用 invoke_agent 约定。在 Jaeger 中可沿父子关系查看编排器、模型
    调用、跨进程 A2A、专业智能体以及数据库查询；不依赖专用 GenAI 徽标界面。
    """
    if not settings.OTEL_ENABLED:
        yield None
        return

    from opentelemetry.trace import SpanKind

    tracer = get_tracer("ecommerce.agent")
    with tracer.start_as_current_span(
        f"invoke_agent {agent_name}",
        kind=SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.system", "openai")
        enrich_span_with_session(agent_name)
        try:
            yield span
        except Exception as e:
            from opentelemetry import trace as trace_api

            span.record_exception(e)
            span.set_status(trace_api.StatusCode.ERROR, str(e))
            raise


@contextmanager
def a2a_call_span(source_agent: str, target_agent: str, target_url: str):
    """为编排器跨进程 A2A 调用创建客户端跨度。

    使用 SpanKind.CLIENT 和 invoke_agent 约定，记录目标智能体；
    httpx 插桩将追踪上下文传播到下游。
    """
    from opentelemetry.trace import SpanKind

    tracer = get_tracer("ecommerce.orchestrator")
    with tracer.start_as_current_span(
        f"invoke_agent {target_agent}",
        kind=SpanKind.CLIENT,
    ) as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.system", "openai")
        span.set_attribute("gen_ai.agent.name", target_agent)
        span.set_attribute("agent.source", source_agent)
        span.set_attribute("agent.target_url", target_url)
        enrich_span_with_session()
        try:
            yield span
        except Exception as e:
            from opentelemetry import trace as trace_api

            span.record_exception(e)
            span.set_status(trace_api.StatusCode.ERROR, str(e))
            raise


@contextmanager
def tool_call_span(tool_name: str):
    """为一次工具调用创建跨度，记录工具名、耗时与成功或失败。

    跨度挂在当前活动追踪上下文下。
    """
    if not settings.OTEL_ENABLED:
        yield None
        return

    from opentelemetry.trace import SpanKind

    tracer = get_tracer("ecommerce.agent")
    with tracer.start_as_current_span(
        f"tool {tool_name}",
        kind=SpanKind.INTERNAL,
        attributes={"tool.name": tool_name},
    ) as span:
        try:
            yield span
        except Exception as e:
            from opentelemetry import trace as trace_api

            span.record_exception(e)
            span.set_status(trace_api.StatusCode.ERROR, str(e))
            raise


def traced_tool(fn: Callable) -> Callable:
    """给 MAF 工具函数增加 OTel 跨度。

    仅在 MAF 未原生输出工具跨度时使用；装饰器排列保持：
        @tool(name="search_products", description="...")
        @traced_tool
        async def search_products(...) -> ...:
    """
    tracer = get_tracer("ecommerce")

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not settings.OTEL_ENABLED:
            return await fn(*args, **kwargs)

        with tracer.start_as_current_span(
            "agent.tool_call",
            attributes={"tool.name": fn.__name__},
        ) as span:
            try:
                result = await fn(*args, **kwargs)
                span.set_attribute("tool.success", True)
                return result
            except Exception as e:
                from opentelemetry import trace as trace_api

                span.record_exception(e)
                span.set_status(trace_api.StatusCode.ERROR, str(e))
                span.set_attribute("tool.success", False)
                raise

    return wrapper


# 内部插桩辅助函数


def _setup_log_provider(resource: Any, endpoint: str) -> None:
    """将 Python logging 连接到 OTel LoggerProvider 并经 OTLP 导出。

    日志记录包含正文、级别、trace_id/span_id 和服务资源属性。接收端
    必须支持 OTLP 日志；Jaeger 本身不提供结构化日志存储。过滤器阻止
    OTel 内部日志重新进入导出管线，避免递归循环。
    """
    import logging as _logging

    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        try:
            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

            log_exporter = OTLPLogExporter(endpoint=endpoint, insecure=True)
        except ImportError:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

            log_exporter = OTLPLogExporter(endpoint=f"{endpoint}/v1/logs")

        log_provider = LoggerProvider(resource=resource)
        log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        set_logger_provider(log_provider)

        # 将 Python 根日志器桥接为 OTel 日志记录。
        handler = LoggingHandler(level=_logging.DEBUG, logger_provider=log_provider)

        # 阻止 OTel 自身日志重新进入导出管线。
        class _NoOtelLoopFilter(_logging.Filter):
            def filter(self, record: _logging.LogRecord) -> bool:
                return not record.name.startswith("opentelemetry")

        handler.addFilter(_NoOtelLoopFilter())
        _logging.getLogger().addHandler(handler)
        logger.info("OTel 日志提供器已初始化；日志导出需要支持 OTLP 日志的接收端")
    except Exception:
        logger.warning("OTel 日志提供器初始化失败，无法导出结构化日志", exc_info=True)


def _instrument_openai() -> None:
    """为 OpenAI Python SDK 安装 GenAI 语义插桩。

    在模型调用中添加系统、操作、请求与响应模型、结束原因和输入输出
    token 数等属性及指标。支持 AsyncOpenAI 与 AsyncAzureOpenAI。
    """
    try:
        from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

        OpenAIInstrumentor().instrument()
        logger.info("OpenAI SDK instrumented with GenAI semantic conventions")
    except ImportError:
        logger.warning(
            "opentelemetry-instrumentation-openai-v2 not installed — "
            "LLM spans will appear as raw HTTP spans without model/token details. "
            "Run: uv add opentelemetry-instrumentation-openai-v2"
        )
    except Exception:
        logger.warning("Failed to instrument OpenAI SDK", exc_info=True)


def _instrument_httpx() -> None:
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception:
        logger.warning("Failed to instrument httpx — LLM and A2A call spans may be missing")


def _instrument_asyncpg() -> None:
    try:
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor

        AsyncPGInstrumentor().instrument()
    except Exception:
        logger.warning("Failed to instrument asyncpg — DB query spans may be missing")


def _instrument_logging() -> None:
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        LoggingInstrumentor().instrument(set_logging_format=False)
    except Exception:
        logger.warning("Failed to instrument logging")
