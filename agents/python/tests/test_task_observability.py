"""任务遥测：阶段耗时、Trace关联及业务状态与HTTP成功的区别。"""

from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from shared.task_observability import TaskObservabilityMiddleware


async def test_task_phase_trace_headers_and_low_cardinality_metrics(monkeypatch):
    import shared.task_observability as mod

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(mod.trace, "get_tracer", provider.get_tracer)
    monkeypatch.setattr(mod.settings, "OTEL_ENABLED", True)
    metric_attrs = []

    class Instrument:
        def add(self, value, attrs):
            metric_attrs.append(attrs)

        def record(self, value, attrs):
            metric_attrs.append(attrs)

    monkeypatch.setattr(mod, "instruments", lambda: (Instrument(), Instrument()))
    app = FastAPI()
    app.add_middleware(TaskObservabilityMiddleware)

    @app.post("/api/tasks/{task_id}/next")
    async def step(task_id: str):
        return {"status": "needs_review"}

    @app.post("/api/tasks/{task_id}/recover")
    async def recover(task_id: str):
        raise HTTPException(409, "仍在执行")

    task_id = str(uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/tasks/{task_id}/next", json={"private_question": "must not be a tag"})
        assert response.json()["status"] == "needs_review"
        assert "task;dur=" in response.headers["server-timing"]
        assert len(response.headers["x-trace-id"]) == 32
        conflict = await client.post(f"/api/tasks/{task_id}/recover")
        assert conflict.status_code == 409
    spans = exporter.get_finished_spans()
    assert spans[0].attributes["task.id"] == task_id
    assert spans[1].attributes["http.response.status_code"] == 409
    assert all(set(attrs) == {"phase", "status_class"} for attrs in metric_attrs)
    assert "must not be a tag" not in str(spans[0].attributes)
    provider.shutdown()


async def test_observation_failure_never_reexecutes_or_breaks_business(monkeypatch):
    import shared.task_observability as mod

    monkeypatch.setattr(mod.settings, "OTEL_ENABLED", True)

    def unavailable(*args, **kwargs):
        raise RuntimeError("telemetry is unavailable")

    monkeypatch.setattr(mod.trace, "get_tracer", unavailable)
    monkeypatch.setattr(mod, "instruments", unavailable)
    calls = []
    app = FastAPI()
    app.add_middleware(TaskObservabilityMiddleware)

    @app.post("/api/tasks")
    async def create():
        calls.append("created")
        return {"status": "pending"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/tasks")
    assert response.status_code == 200 and response.json() == {"status": "pending"}
    assert calls == ["created"]
    assert "server-timing" in response.headers


async def test_observation_failure_cannot_mask_original_business_error(monkeypatch):
    import pytest

    import shared.task_observability as mod

    monkeypatch.setattr(mod.settings, "OTEL_ENABLED", True)

    def unavailable():
        raise RuntimeError("metric failure")

    monkeypatch.setattr(mod, "instruments", unavailable)
    calls = []

    async def business(scope, receive, send):
        calls.append("once")
        raise ValueError("original business failure")

    middleware = TaskObservabilityMiddleware(business)
    with pytest.raises(ValueError, match="original business failure"):
        await middleware({"type": "http", "path": "/api/tasks", "method": "POST"}, None, None)
    assert calls == ["once"]
