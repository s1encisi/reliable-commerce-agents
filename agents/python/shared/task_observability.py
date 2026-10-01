"""任务 HTTP 阶段耗时与 Trace 关联；观测故障不能重放或破坏业务响应。"""

import logging
import time
from contextlib import ExitStack
from functools import lru_cache
from uuid import UUID

from opentelemetry import trace

from shared.config import settings
from shared.telemetry import get_meter

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def instruments():
    meter = get_meter("commerce.tasks")
    return meter.create_counter("commerce.task.requests"), meter.create_histogram("commerce.task.duration", unit="s")


def observe(operation, *args, **kwargs):
    """只隔离可选观测操作；业务调用与取消不经过此保护。"""
    try:
        return operation(*args, **kwargs)
    except Exception:
        logger.warning("task.observation_unavailable", exc_info=True)
        return None


class TaskObservabilityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/api/tasks" or path.startswith("/api/tasks/")):
            return await self.app(scope, receive, send)
        parts = path.strip("/").split("/")
        phase = "create" if len(parts) == 2 else "edit" if scope["method"] == "PATCH" else "read"
        if len(parts) == 4 and parts[3] in {"plan", "next", "recover", "confirm"}:
            phase = parts[3]
        task_id = None
        if len(parts) >= 3:
            try:
                task_id = str(UUID(parts[2]))
            except ValueError:
                pass
        start = time.perf_counter()
        status = 500
        stack = ExitStack()

        def start_span():
            return stack.enter_context(
                trace.get_tracer("commerce.tasks").start_as_current_span(
                    f"task.{phase}",
                    record_exception=False,
                    set_status_on_exception=False,
                )
            )

        span = observe(start_span) if settings.OTEL_ENABLED else None
        if span is not None:
            observe(span.set_attribute, "task.phase", phase)
            if task_id:
                observe(span.set_attribute, "task.id", task_id)

        async def measured_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                headers = list(message.get("headers", []))
                headers.append((b"server-timing", f"task;dur={(time.perf_counter() - start) * 1000:.3f}".encode()))
                context = observe(span.get_span_context) if span is not None else None
                if context is not None and context.is_valid:
                    headers.append((b"x-trace-id", f"{context.trace_id:032x}".encode()))
                message["headers"] = headers
            await send(message)

        def record_metrics():
            requests, latency = instruments()
            attrs = {"phase": phase, "status_class": f"{status // 100}xx"}
            requests.add(1, attrs)
            latency.record(time.perf_counter() - start, attrs)

        try:
            await self.app(scope, receive, measured_send)
        finally:
            if span is not None:
                observe(span.set_attribute, "http.response.status_code", status)
            # HTTP 成功不等于任务完成，业务结果由状态与验收回执单独表达。
            if settings.OTEL_ENABLED:
                observe(record_metrics)
            observe(stack.close)
