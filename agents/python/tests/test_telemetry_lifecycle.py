"""Real FastAPI lifespan must install server tracing before the ASGI stack is built."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("kind", ["orchestrator", "specialist"])
def test_server_spans_survive_actual_application_startup(kind):
    code = """
import json
import sys
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from shared.config import settings
from shared.telemetry import instrument_fastapi

settings.OTEL_ENABLED = True
exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
def initialize(*args, **kwargs):
    trace.set_tracer_provider(provider)

async def no_database():
    pass

if sys.argv[1] == "orchestrator":
    import orchestrator.main as main
    main.setup_telemetry = initialize
    main.init_db_pool = no_database
    main.close_db_pool = no_database
    app, path = main.app, "/openapi.json"
else:
    from shared.agent_host import create_agent_app
    async def startup(app):
        initialize()
        instrument_fastapi(app)
    app = create_agent_app(agent=object(), agent_name="read-probe", port=8081, on_startup=startup)
    path = "/.well-known/agent-card.json"

with TestClient(app) as client:
    response = client.get(path)
provider.force_flush()
spans = exporter.get_finished_spans()
print(json.dumps({"status": response.status_code, "server_spans": sum(s.kind == trace.SpanKind.SERVER for s in spans)}))
provider.shutdown()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, kind],
        env={
            **os.environ,
            "APP_ENV_FILE": "/dev/null",
            "ENVIRONMENT": "test",
            "OTEL_ENABLED": "true",
            "OTEL_PYTHON_FASTAPI_EXCLUDED_URLS": "/health",
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    record = json.loads(result.stdout)
    assert record["status"] == 200
    assert record["server_spans"] >= 1
