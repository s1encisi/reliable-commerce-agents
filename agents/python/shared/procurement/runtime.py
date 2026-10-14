"""应用生命周期接线；模型服务、租约与MAF执行器保持可独立测试。"""

from contextlib import ExitStack

from opentelemetry import trace

from shared.config import settings
from shared.procurement.dispatch import dispatch_step, finalize_task, reuse_key
from shared.procurement.planning import generate_plan
from shared.procurement.runner import ProcurementRunner
from shared.task_observability import observe


def observed(callback, phase):
    async def run(task, *args, **kwargs):
        stack = ExitStack()
        span = (
            observe(
                lambda: stack.enter_context(
                    trace.get_tracer("commerce.procurement").start_as_current_span(
                        f"procurement.{phase}", record_exception=False, set_status_on_exception=False
                    )
                )
            )
            if settings.OTEL_ENABLED
            else None
        )
        if span is not None:
            for name, value in {
                "task.id": str(task["id"]),
                "procurement.phase": phase,
                "procurement.plan_revision": task["plan_revision"],
                "procurement.mode": task["execution_mode"],
            }.items():
                observe(span.set_attribute, name, value)
            if args and hasattr(args[0], "capability"):
                observe(span.set_attribute, "procurement.capability", args[0].capability)
        try:
            return await callback(task, *args, **kwargs)
        finally:
            observe(stack.close)

    return run


def make_runner(pool):
    return ProcurementRunner(
        pool,
        observed(generate_plan, "planning"),
        observed(dispatch_step, "step"),
        observed(finalize_task, "verify"),
        reuse_key=reuse_key,
        enabled=settings.PROCUREMENT_ENABLED,
        model_authorized=settings.PROCUREMENT_MODEL_AUTHORIZED,
        allowlist=settings.PROCUREMENT_ALLOWLIST,
        run_timeout_seconds=settings.PROCUREMENT_RUN_TIMEOUT_SECONDS,
    )
