"""Verify real local OTLP exports, then restore the default nine-service showcase.

A marked task uses real HTTP and SQL; a separate agent-card probe verifies trace propagation.
No model, payment, new product endpoint, or database reset is involved.
"""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
APPS = [
    "orchestrator",
    "product-discovery",
    "order-management",
    "pricing-promotions",
    "review-sentiment",
    "inventory-fulfillment",
]
EXPECTED = set(APPS) | {"db", "redis", "web"}
COLLECTOR = "ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector-contrib:0.123.0"
SOURCES = [
    "scripts/verify_local_telemetry.py",
    "docker-compose.telemetry-check.yml",
    "docker/observability/local-verification.yml",
    "agents/python/orchestrator/main.py",
    "agents/python/shared/agent_host.py",
    "agents/python/shared/telemetry.py",
    "agents/python/shared/task_observability.py",
    "agents/python/shared/task_state.py",
    "agents/python/shared/task_acceptance.py",
    "agents/python/orchestrator/routes/tasks.py",
    "agents/python/shared/config.py",
    "agents/python/uv.lock",
]


def run(*arguments, capture=False):
    result = subprocess.run(
        arguments, cwd=ROOT, text=True, capture_output=capture, check=True, timeout=240
    )
    return result.stdout if capture else ""


def compose(overlay, *arguments, capture=False):
    command = [
        "docker",
        "compose",
        "--env-file",
        str(ROOT / ".env.showcase.local"),
        "-f",
        str(ROOT / "docker-compose.showcase.yml"),
    ]
    if overlay:
        command.extend(["-f", str(ROOT / "docker-compose.telemetry-check.yml")])
    return run(*command, *arguments, capture=capture)


def rows_from_json(text):
    return (
        json.loads(text)
        if text.strip().startswith("[")
        else [json.loads(line) for line in text.splitlines() if line.strip()]
    )


def state():
    rows = rows_from_json(compose(False, "ps", "--format", "json", capture=True))
    return [
        {
            "service": row["Service"],
            "state": row["State"],
            "health": row.get("Health"),
            "published": [
                entry
                for entry in (row.get("Publishers") or [])
                if entry.get("PublishedPort")
            ],
        }
        for row in rows
    ]


async def snapshot():
    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        if await conn.fetchval("SELECT current_database()") != "reliable_commerce_demo":
            raise RuntimeError(
                "Only the synthetic reliable_commerce_demo database is permitted"
            )
        return {
            table: await conn.fetchval(f"SELECT count(*) FROM {table}")
            for table in (
                "orders",
                "returns",
                "tool_approval_requests",
                "agent_tasks",
            )
        }
    finally:
        await conn.close()


async def inside(marker):
    import httpx
    from opentelemetry import metrics, trace
    from shared.config import settings
    from shared.telemetry import setup_telemetry

    if not settings.OTEL_ENABLED or settings.GENAI_CAPTURE_CONTENT:
        raise RuntimeError(
            "Explicit enabled telemetry with content capture disabled is required"
        )
    setup_telemetry("ecommerce.telemetry-verifier")
    async with httpx.AsyncClient(
        base_url="http://orchestrator:8080", timeout=45, trust_env=False
    ) as client:
        health = await client.get("http://telemetry-check:13133")
        health.raise_for_status()
        login = await client.post(
            "/api/auth/login",
            json={"email": "customer@example.test", "password": "DemoPass123!"},
        )
        login.raise_for_status()
        token = login.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        capability = await client.get("/api/runtime/capabilities")
        capability.raise_for_status()
        if capability.json().get("offline_demo") is not True:
            raise RuntimeError("Refuse non-offline task execution")
        phases = []

        async def post(phase, path, body):
            response = await client.post(path, json=body)
            response.raise_for_status()
            phases.append(
                {
                    "phase": phase,
                    "trace_id": response.headers.get("x-trace-id"),
                    "http_status": response.status_code,
                }
            )
            return response.json()

        task = await post(
            "create",
            "/api/tasks",
            {
                "goal": capability.json()["task_demo_goal"],
                "constraints": [marker],
                "requirements": {"min_items": 2},
            },
        )
        task = await post(
            "plan", f"/api/tasks/{task['id']}/plan", {"revision": task["revision"]}
        )
        if task["plan"].get("source") != "deterministic_demo":
            raise RuntimeError("Refuse a model-generated plan")
        for _ in range(8):
            if task["status"] != "ready":
                break
            task = await post(
                "next", f"/api/tasks/{task['id']}/next", {"revision": task["revision"]}
            )
        if task["status"] != "needs_review" or marker not in task["constraints"]:
            raise RuntimeError(
                "The unverified natural-language constraint must remain needs_review"
            )

        # Separate, explicitly synthetic read probe. HTTPX propagates context on the real request.
        with trace.get_tracer("commerce.verification").start_as_current_span(
            "verification.agent_card_read"
        ) as span:
            span.set_attribute("verification.kind", "synthetic_read_probe_no_model")
            probe_trace = f"{span.get_span_context().trace_id:032x}"
            card = await client.get(
                "http://product-discovery:8081/.well-known/agent-card.json",
                headers={"Authorization": ""},
            )
            card.raise_for_status()
            if card.json()["name"] != "product-discovery":
                raise RuntimeError("Unexpected agent-card target")
    trace.get_tracer_provider().force_flush(timeout_millis=10000)
    metrics.get_meter_provider().force_flush(timeout_millis=10000)
    return {
        "task_id": task["id"],
        "task_status": task["status"],
        "plan_source": task["plan"]["source"],
        "task_phases": phases,
        "read_probe_trace_id": probe_trace,
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "marker_sha256": hashlib.sha256(marker.encode()).hexdigest(),
        "database_after": await snapshot(),
        "python": platform.python_version(),
    }


def attributes(values):
    return {item["key"]: next(iter(item["value"].values())) for item in values}


def read_exports(directory, signal):
    documents = [
        json.loads(line)
        for line in (directory / f"{signal}.jsonl").read_text().splitlines()
        if line.strip()
    ]
    return documents


def analyze(directory, probe, marker):
    spans = []
    for doc in read_exports(directory, "traces"):
        for resource in doc.get("resourceSpans", []):
            service = attributes(resource["resource"].get("attributes", [])).get(
                "service.name"
            )
            for scope in resource.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    spans.append(
                        {
                            **span,
                            "service": service,
                            "instrumentation_scope": scope.get("scope", {}).get("name"),
                            "attrs": attributes(span.get("attributes", [])),
                        }
                    )
    index = {(s["traceId"], s["spanId"]): s for s in spans}

    def descendant(child, ancestor):
        parent = child.get("parentSpanId")
        seen = set()
        while parent and parent not in seen:
            if parent == ancestor["spanId"]:
                return True
            seen.add(parent)
            parent = index.get((child["traceId"], parent), {}).get("parentSpanId")
        return False

    business = []
    for phase in probe["task_phases"]:
        group = [s for s in spans if s["traceId"] == phase["trace_id"]]
        servers = [
            s
            for s in group
            if s["service"] == "ecommerce.orchestrator"
            and s.get("kind") in (2, "SPAN_KIND_SERVER")
        ]
        tasks = [s for s in group if s["name"] == f"task.{phase['phase']}"]
        sql = [
            s
            for s in group
            if s["service"] == "ecommerce.orchestrator"
            and s["instrumentation_scope"] == "opentelemetry.instrumentation.asyncpg"
            and (
                s["attrs"].get("db.system") == "postgresql"
                or s["attrs"].get("db.system.name") == "postgresql"
            )
        ]
        linked = [
            (server, task, query)
            for server in servers
            for task in tasks
            for query in sql
            if descendant(task, server) and descendant(query, task)
        ]
        business.append(
            {
                **phase,
                "server_spans": len(servers),
                "task_spans": len(tasks),
                "sql_spans": len(sql),
                "real_parent_child_chain": bool(linked),
                "example_chain": [
                    {
                        "name": s["name"],
                        "service": s["service"],
                        "span_id": s["spanId"],
                        "parent_span_id": s.get("parentSpanId"),
                        "instrumentation_scope": s["instrumentation_scope"],
                        "kind": s.get("kind"),
                    }
                    for s in linked[0]
                ]
                if linked
                else [],
            }
        )
    group = [s for s in spans if s["traceId"] == probe["read_probe_trace_id"]]
    roots = [s for s in group if s["name"] == "verification.agent_card_read"]
    clients = [
        s
        for s in group
        if s.get("kind") in (3, "SPAN_KIND_CLIENT")
        and s["instrumentation_scope"] == "opentelemetry.instrumentation.httpx"
    ]
    servers = [
        s
        for s in group
        if s["service"] == "ecommerce.product-discovery"
        and s.get("kind") in (2, "SPAN_KIND_SERVER")
    ]
    probe_linked = [
        (root, client, server)
        for root in roots
        for client in clients
        for server in servers
        if descendant(client, root) and server.get("parentSpanId") == client["spanId"]
    ]
    series = {}
    metric_privacy = True
    for doc in read_exports(directory, "metrics"):
        for resource in doc.get("resourceMetrics", []):
            for scope in resource.get("scopeMetrics", []):
                for metric in scope.get("metrics", []):
                    if metric["name"] not in {
                        "commerce.task.requests",
                        "commerce.task.duration",
                    }:
                        continue
                    data = metric.get("sum") or metric.get("histogram") or {}
                    for point in data.get("dataPoints", []):
                        attrs = attributes(point.get("attributes", []))
                        metric_privacy &= set(attrs) == {"phase", "status_class"}
                        key = (metric["name"], tuple(sorted(attrs.items())))
                        series[key] = {
                            "name": metric["name"],
                            "attribute_keys": sorted(attrs),
                            "phase": attrs.get("phase"),
                            "status_class": attrs.get("status_class"),
                            "count_or_value": point.get(
                                "asInt", point.get("asDouble", point.get("count"))
                            ),
                        }
    raw = "\n".join(
        (directory / f"{name}.jsonl").read_text()
        for name in ("traces", "metrics", "logs")
    )
    prometheus = (directory / "prometheus.txt").read_text()
    raw += "\n" + prometheus
    jwt_candidates = re.findall(
        r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", raw
    )
    token_present = any(
        hashlib.sha256(token.encode()).hexdigest() == probe["token_sha256"]
        for token in jwt_candidates
    )
    privacy = {
        "task_marker_absent": marker not in raw,
        "bearer_text_absent": re.search(r"\bbearer\s+", raw, re.IGNORECASE) is None,
        "this_access_token_absent": not token_present,
        "task_metric_labels_low_cardinality": metric_privacy,
        "synthetic_email_in_raw_auth_logs": "customer@example.test"
        in (directory / "logs.jsonl").read_text(),
        "note": "Synthetic auth email in logs is reported separately; task UUIDs may appear on spans, not metric labels.",
    }
    expected_counts = {}
    for phase in probe["task_phases"]:
        expected_counts[phase["phase"]] = expected_counts.get(phase["phase"], 0) + 1
    metric_counts_verified = all(
        any(
            row["name"] == name
            and row["phase"] == phase
            and row["status_class"] == "2xx"
            and float(row["count_or_value"] or 0) >= count
            for row in series.values()
        )
        for phase, count in expected_counts.items()
        for name in ("commerce.task.requests", "commerce.task.duration")
    )
    return {
        "task_metric_counts_verified": metric_counts_verified,
        "business_http_to_sql": business,
        "cross_service_read_probe": {
            "trace_id": probe["read_probe_trace_id"],
            "scope": "synthetic agent-card GET, not model/tool execution",
            "real_parent_child_chain": bool(probe_linked),
            "example_chain": [
                {
                    "name": s["name"],
                    "service": s["service"],
                    "span_id": s["spanId"],
                    "parent_span_id": s.get("parentSpanId"),
                    "instrumentation_scope": s["instrumentation_scope"],
                    "kind": s.get("kind"),
                }
                for s in probe_linked[0]
            ]
            if probe_linked
            else [],
        },
        "task_metrics": list(series.values()),
        "prometheus_task_metrics_present": "commerce_task_requests_total" in prometheus
        and "commerce_task_duration_seconds" in prometheus,
        "privacy": privacy,
        "genai_operation_spans": sum(
            "gen_ai.operation.name" in s["attrs"] for s in spans
        ),
        "exported_spans": len(spans),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--snapshot", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--marker", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--capture-dir", type=Path)
    args = parser.parse_args()
    if args.snapshot:
        print(json.dumps(asyncio.run(snapshot())))
        return
    if args.inside:
        print(json.dumps(asyncio.run(inside(args.marker)), ensure_ascii=False))
        return
    if not args.output or not args.capture_dir:
        parser.error("--output and --capture-dir are required")
    if args.output.exists() or args.capture_dir.exists():
        parser.error(
            "Choose new output/capture paths; existing evidence is never overwritten"
        )
    export_directory = args.capture_dir.resolve() / "collector-files"
    os.environ["LOCAL_TELEMETRY_EXPORT_DIR"] = str(export_directory)
    initial = state()
    if {s["service"] for s in initial} != EXPECTED or any(
        s["state"] != "running" or s["health"] != "healthy" for s in initial
    ):
        parser.error("Default nine-service showcase must already be healthy")
    collector_image = json.loads(
        run("docker", "image", "inspect", COLLECTOR, capture=True)
    )[0]
    if compose(True, "ps", "-a", "-q", "telemetry-check", capture=True).strip():
        parser.error(
            "An existing verification collector must be inspected and its exports preserved first"
        )
    args.capture_dir.mkdir(parents=True, mode=0o700)
    # Collector uses UID 10001. The enclosing capture directory stays private;
    # only its mounted child is writable inside the otherwise scratch-based image.
    export_directory.mkdir(mode=0o777)
    export_directory.chmod(0o777)
    marker = f"OTEL_PRIVACY_MARKER_{uuid4().hex}"
    before = json.loads(
        compose(
            False,
            "run",
            "--rm",
            "--no-deps",
            "seed",
            "/app/.venv/bin/python",
            "-m",
            "scripts.verify_local_telemetry",
            "--snapshot",
            capture=True,
        )
    )
    probe = None
    collector_id = None
    restored = None
    error = None
    recovery_errors = []
    collector_removed = False
    try:
        compose(True, "up", "-d", "--no-deps", "telemetry-check")
        collector_id = compose(
            True, "ps", "-q", "telemetry-check", capture=True
        ).strip()
        compose(True, "up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", *APPS)
        result = compose(
            True,
            "exec",
            "-T",
            "orchestrator",
            "/app/.venv/bin/python",
            "-m",
            "scripts.verify_local_telemetry",
            "--inside",
            "--marker",
            marker,
            capture=True,
        )
        probe = json.loads(result)
        time.sleep(
            12
        )  # One bounded wait covers SDK metric export and collector batching.
        prometheus = compose(
            True,
            "exec",
            "-T",
            "orchestrator",
            "/app/.venv/bin/python",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://telemetry-check:9464/metrics').read().decode())",
            capture=True,
        )
        (args.capture_dir / "prometheus.txt").write_text(prometheus)
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
        error = type(exc).__name__
    finally:
        # Restore each part independently: a missing export must not skip collector cleanup.
        try:
            compose(
                False, "up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", *APPS
            )
        except (OSError, subprocess.SubprocessError) as exc:
            recovery_errors.append(
                {"stage": "restore_apps", "error_type": type(exc).__name__}
            )
        if not collector_id:
            try:
                collector_id = (
                    compose(
                        True, "ps", "-a", "-q", "telemetry-check", capture=True
                    ).strip()
                    or None
                )
                collector_removed = collector_id is None
            except (OSError, subprocess.SubprocessError) as exc:
                recovery_errors.append(
                    {"stage": "locate_collector", "error_type": type(exc).__name__}
                )
        if collector_id:
            try:
                diagnostics = subprocess.run(
                    ["docker", "logs", collector_id],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
                (args.capture_dir / "collector.log").write_text(
                    diagnostics.stdout + diagnostics.stderr
                )
            except (OSError, subprocess.SubprocessError) as exc:
                recovery_errors.append(
                    {"stage": "collector_diagnostics", "error_type": type(exc).__name__}
                )
            try:
                compose(True, "stop", "telemetry-check")
            except (OSError, subprocess.SubprocessError) as exc:
                recovery_errors.append(
                    {"stage": "stop_collector", "error_type": type(exc).__name__}
                )
            for name in ("traces", "metrics", "logs"):
                try:
                    run(
                        "docker",
                        "cp",
                        f"{collector_id}:/var/lib/otel-verification/{name}.jsonl",
                        str(args.capture_dir / f"{name}.jsonl"),
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    recovery_errors.append(
                        {"stage": f"capture_{name}", "error_type": type(exc).__name__}
                    )
            try:
                compose(True, "rm", "-f", "-s", "telemetry-check")
                collector_removed = True
            except (OSError, subprocess.SubprocessError) as exc:
                recovery_errors.append(
                    {"stage": "remove_collector", "error_type": type(exc).__name__}
                )
        try:
            restored = state()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            recovery_errors.append(
                {"stage": "inspect_restoration", "error_type": type(exc).__name__}
            )
            restored = []
    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": "Local OTLP integration, real HTTP and SQL; separate synthetic cross-service read probe. No LLM/payment.",
        "collector_image": COLLECTOR,
        "collector_image_id": collector_image["Id"],
        "collector_repo_digests": collector_image.get("RepoDigests", []),
        "before_database_counts": before,
        "probe": probe,
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_text().encode()).hexdigest()
            for name in SOURCES
        },
        "restored_services": restored,
        "error_type": error,
        "recovery_errors": recovery_errors,
        "collector_removed": collector_removed,
    }
    if probe:
        try:
            report.update(analyze(args.capture_dir, probe, marker))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            error = f"analyze:{type(exc).__name__}"
            report["error_type"] = error
    published = [
        (s["service"], p["URL"], p["PublishedPort"])
        for s in restored
        for p in s["published"]
    ]
    restore_ok = (
        {s["service"] for s in restored} == EXPECTED
        and all(s["health"] == "healthy" for s in restored)
        and published == [("web", "127.0.0.1", 3020)]
    )
    disabled = {}
    for app in APPS:
        try:
            info = json.loads(
                run(
                    "docker",
                    "inspect",
                    f"reliable-commerce-showcase-{app}-1",
                    capture=True,
                )
            )[0]
            environment = dict(value.split("=", 1) for value in info["Config"]["Env"])
            disabled[app] = environment.get("OTEL_ENABLED") == "false"
        except (
            OSError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
            IndexError,
        ) as exc:
            recovery_errors.append(
                {"stage": f"inspect_{app}_mode", "error_type": type(exc).__name__}
            )
            disabled[app] = False
    restore_ok = restore_ok and collector_removed and all(disabled.values())
    report["restored_otel_disabled"] = disabled
    report["restoration_verified"] = restore_ok
    report["passed"] = bool(
        not error
        and not recovery_errors
        and restore_ok
        and probe
        and probe["task_status"] == "needs_review"
        and all(
            row["real_parent_child_chain"] for row in report["business_http_to_sql"]
        )
        and report["cross_service_read_probe"]["real_parent_child_chain"]
        and report["task_metrics"]
        and report["task_metric_counts_verified"]
        and report["prometheus_task_metrics_present"]
        and all(
            report["privacy"][key]
            for key in (
                "task_marker_absent",
                "bearer_text_absent",
                "this_access_token_absent",
                "task_metric_labels_low_cardinality",
            )
        )
        and report["genai_operation_spans"] == 0
        and all(
            probe["database_after"][key] == before[key]
            for key in ("orders", "returns", "tool_approval_requests")
        )
        and probe["database_after"]["agent_tasks"] == before["agent_tasks"] + 1
    )
    report["raw_capture_sha256"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in args.capture_dir.iterdir()
        if p.is_file()
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "task_status": probe["task_status"] if probe else None,
                "restoration_verified": restore_ok,
                "report": str(args.output),
            }
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
