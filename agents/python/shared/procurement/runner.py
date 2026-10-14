"""Bounded PostgreSQL-leased procurement worker using real MAF frozen workflows."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import time
from datetime import datetime
from uuid import uuid4

from shared.campaign_budget import BudgetError
from shared.procurement import state
from shared.procurement.models import (
    LayerToken,
    Lease,
    Plan,
    RunSignal,
    StepOutcome,
    canonical_hash,
    current_procurement_binding,
)
from shared.procurement.workflow import PhaseCheckpointStorage, compile_plan

logger = logging.getLogger(__name__)
_POLICY_STOPS = {"POLICY_REJECTED", "STALE_PLAN", "SERVICE_UNAVAILABLE", "READ_ONLY_POLICY", "MODEL_NOT_AUTHORIZED"}
_BUDGET_STOPS = {"MODEL_CALL_LIMIT", "BUDGET_EXHAUSTED"}


def _temporary(outcome):
    return outcome.status == "failed" and outcome.retryable and outcome.error_code not in _POLICY_STOPS | _BUDGET_STOPS


async def _call(callback, *args, **kwargs):
    value = callback(*args, **kwargs)
    return await value if inspect.isawaitable(value) else value


class ProcurementRunner:
    def __init__(
        self,
        pool,
        planner,
        dispatcher,
        finalizer=None,
        *,
        reuse_key=None,
        event_hook=None,
        enabled=False,
        model_authorized=False,
        allowlist=(),
        run_timeout_seconds=180,
        lease_seconds=30,
        heartbeat_seconds=10,
        max_model_calls=24,
        concurrency=2,
        max_repairs=1,
        worker_id=None,
    ):
        if not 0 < heartbeat_seconds < lease_seconds:
            raise ValueError("Heartbeat must be positive and shorter than the lease")
        if concurrency not in {1, 2} or max_repairs not in {0, 1}:
            raise ValueError("Procurement allows at most two parallel nodes and one repair")
        self.pool = pool
        self.planner = planner
        self.dispatcher = dispatcher
        self.finalizer = finalizer
        self.reuse_key = reuse_key
        self.event_hook = event_hook
        self.enabled = enabled
        self.model_authorized = model_authorized
        self.allowlist = (
            {x.strip() for x in allowlist.split(",") if x.strip()} if isinstance(allowlist, str) else set(allowlist)
        )
        self.run_timeout_seconds = run_timeout_seconds
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.max_model_calls = min(max_model_calls, 24)
        self.concurrency = concurrency
        self.max_repairs = max_repairs
        self.worker_id = worker_id or str(uuid4())

    async def _hook(self, name, task, payload):
        if self.event_hook is not None:
            await _call(self.event_hook, name, task, payload)

    async def serve(self, stop_event: asyncio.Event):
        while not stop_event.is_set():
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Claim/load/storage failures leave durable state for lease recovery;
                # they must not silently kill the worker that serves other queued tasks.
                logger.exception("procurement.worker_iteration_failed")
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=1)
                except TimeoutError:
                    pass

    async def run_once(self, task_id=None) -> bool:
        if not self.enabled:
            return False
        lease = await state.claim_task(
            self.pool,
            self.worker_id,
            task_id=task_id,
            lease_seconds=self.lease_seconds,
            run_timeout_seconds=self.run_timeout_seconds,
        )
        if lease is None:
            return False
        # A caller may be an HTTP handler. Never inherit its connection, identity,
        # approval state, session or model-call ContextVars into the background job.
        worker = asyncio.create_task(self._execute(lease), context=contextvars.Context())
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(lease, worker, lost), context=contextvars.Context())
        try:
            await worker
        except asyncio.CancelledError:
            if not lost.is_set():
                raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        return True

    async def _heartbeat(self, lease, worker, lost):
        while not worker.done():
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                renewed = await state.heartbeat(self.pool, lease, self.lease_seconds)
            except Exception:
                logger.exception("procurement.heartbeat_failed task=%s", lease.task_id)
                renewed = False
            if not renewed:
                lost.set()
                worker.cancel()
                return

    async def _execute(self, lease: Lease):
        from shared.context import current_user_email, current_user_role
        from shared.execution_policy import current_execution_policy
        from shared.paid_transport import current_root_run, current_run_deadline

        task = await state.lease_task(self.pool, lease)
        bindings = [
            (current_user_email, current_user_email.set(task["owner_email"])),
            (current_user_role, current_user_role.set(task["owner_role"])),
            (current_execution_policy, current_execution_policy.set("read_only")),
            (current_root_run, current_root_run.set(str(lease.task_id))),
            (current_run_deadline, current_run_deadline.set(datetime.fromisoformat(task["run_deadline"]).timestamp())),
            (
                current_procurement_binding,
                current_procurement_binding.set(
                    {
                        "task_id": str(lease.task_id),
                        "owner_email": lease.owner_email,
                        "lease_token": str(lease.token),
                        "generation": lease.generation,
                        "spec_revision": lease.spec_revision,
                    }
                ),
            ),
        ]
        try:
            if self.allowlist and lease.owner_email not in self.allowlist:
                await state.finish_task(self.pool, lease, "failed", stop_reason="NOT_ALLOWLISTED")
                return
            if task["execution_mode"] == "model" and not self.model_authorized:
                await state.finish_task(self.pool, lease, "failed", stop_reason="MODEL_NOT_AUTHORIZED")
                return
            remaining = datetime.fromisoformat(task["run_deadline"]).timestamp() - time.time()
            if remaining <= 0:
                await state.finish_task(self.pool, lease, "failed", stop_reason="RUN_DEADLINE_EXCEEDED")
                return
            async with asyncio.timeout(remaining):
                await self._drive(lease)
        except state.LeaseLost:
            logger.info("procurement.lease_lost task=%s", lease.task_id)
        except (state.BudgetExceeded, BudgetError):
            await self._finish_if_owned(lease, "budget_exhausted", "MODEL_CALL_LIMIT")
        except TimeoutError:
            await self._finish_if_owned(lease, "failed", "RUN_DEADLINE_EXCEEDED")
        except asyncio.CancelledError:
            # Process shutdown / lease loss: leave running attempts for expired-lease
            # recovery; cancellation cannot release already reserved model charges.
            raise
        except Exception:
            logger.exception("procurement.run_failed task=%s", lease.task_id)
            await self._finish_if_owned(lease, "failed", "RUN_FAILED")
        finally:
            for variable, token in reversed(bindings):
                variable.reset(token)

    async def _finish_if_owned(self, lease, status, reason):
        try:
            await state.finish_task(self.pool, lease, status, stop_reason=reason)
        except state.LeaseLost:
            pass

    async def _plan(self, lease, *, repair=False, failures=None):
        task = await state.begin_planning(self.pool, lease, repair=repair)
        plan = Plan.model_validate(await _call(self.planner, task, repair=repair, failures=failures or []))
        expected_source = "deterministic_demo" if task["execution_mode"] == "offline" else "model"
        if plan.source != expected_source:
            raise ValueError("Planner source does not match the task's fixed execution mode")
        return await state.save_plan(self.pool, lease, plan)

    async def _drive(self, lease):
        failures = []
        ttl_refreshes = 0
        while True:
            task = await state.lease_task(self.pool, lease)
            if task["status"] == "pausing":
                await state.finish_task(self.pool, lease, "paused")
                return
            if task["plan"] is None:
                repair = task["planning_attempts"] > 0
                if repair and task["counters"]["repairs_used"] >= self.max_repairs:
                    await state.finish_task(self.pool, lease, "failed", stop_reason="PLANNING_LIMIT")
                    return
                try:
                    task = await self._plan(lease, repair=repair, failures=failures)
                except (state.BudgetExceeded, BudgetError):
                    raise
                except (ValueError, state.TaskConflict) as exc:
                    failures = [{"code": "INVALID_PLAN", "reason": str(exc), "capabilities": []}]
                    task = await state.lease_task(self.pool, lease)
                    if task["status"] == "pausing":
                        await state.finish_task(self.pool, lease, "paused")
                        return
                    if task["planning_attempts"] >= 2 or self.max_repairs == 0:
                        await state.finish_task(self.pool, lease, "failed", stop_reason="INVALID_PLAN")
                        return
                    continue
            plan = Plan.model_validate(task["plan"])
            if await state.invalidate_expired_receipts(self.pool, lease):
                task = await state.lease_task(self.pool, lease)
            result = await self._run_graph(lease, task, plan)
            task = await state.lease_task(self.pool, lease)
            if result.status == "paused" or task["status"] == "pausing":
                await state.finish_task(self.pool, lease, "paused")
                return
            outcomes = {
                row["id"]: StepOutcome.model_validate(row["outcome"]) for row in task["steps"] if row["outcome"]
            }
            failures = [
                {
                    "step_id": step.id,
                    "code": outcomes[step.id].error_code or outcomes[step.id].status,
                    "reason": outcomes[step.id].message,
                    "capabilities": [step.capability],
                    "affected_product_ids": outcomes[step.id].data.get("affected_product_ids", []),
                    "status": outcomes[step.id].status,
                    "retryable": outcomes[step.id].retryable,
                    "data": outcomes[step.id].data,
                }
                for step in plan.steps
                if step.id in outcomes and outcomes[step.id].status != "succeeded"
            ]
            if result.status == "completed" and len(outcomes) == len(plan.steps) and not failures:
                await self._hook("before_finalize", task, {"outcomes": outcomes})
                final = (
                    await _call(self.finalizer, task, outcomes)
                    if self.finalizer is not None
                    else {"status": "needs_review", "options": [], "stop_reason": "FINALIZER_REQUIRED"}
                )
                if final.get("status") != "needs_replan":
                    status = final.get("status", "needs_review")
                    if status not in {"completed", "needs_review", "failed", "budget_exhausted"}:
                        status = "failed"
                    await state.finish_task(
                        self.pool,
                        lease,
                        status,
                        options=final.get("options", []),
                        stop_reason=final.get("stop_reason"),
                        verification=final.get("verification") if status == "completed" else None,
                    )
                    return
                failures = final.get("failures") or [{"code": "FINAL_VALIDATION_FAILED", "capabilities": []}]
            if any(failure.get("code") == "DEPENDENCY_EXPIRED" for failure in failures):
                if ttl_refreshes >= 1:
                    await state.finish_task(self.pool, lease, "needs_review", stop_reason="EVIDENCE_EXPIRED")
                    return
                ttl_refreshes += 1
                continue
            if any(failure.get("code") in {"MODEL_CALL_LIMIT", "BUDGET_EXHAUSTED"} for failure in failures):
                await state.finish_task(self.pool, lease, "budget_exhausted", stop_reason="MODEL_CALL_LIMIT")
                return
            if any(failure.get("code") in _POLICY_STOPS for failure in failures):
                code = next(failure["code"] for failure in failures if failure.get("code") in _POLICY_STOPS)
                await state.finish_task(self.pool, lease, "failed", stop_reason=code)
                return
            if any(failure.get("status") == "unknown" for failure in failures):
                await state.finish_task(self.pool, lease, "needs_review", stop_reason="UNKNOWN_OUTCOME")
                return
            if any(failure.get("retryable") for failure in failures):
                await state.finish_task(self.pool, lease, "needs_review", stop_reason="TRANSIENT_RETRY_EXHAUSTED")
                return
            if not failures:
                failures = [{"code": "INCOMPLETE_PLAN", "capabilities": []}]
            await state.record_failures(self.pool, lease, failures)
            task = await state.lease_task(self.pool, lease)
            if task["counters"]["repairs_used"] >= self.max_repairs or task["planning_attempts"] >= 2:
                await state.finish_task(self.pool, lease, "needs_review", stop_reason="REPAIR_LIMIT")
                return
            try:
                await self._plan(lease, repair=True, failures=failures)
            except (state.BudgetExceeded, BudgetError):
                raise
            except (ValueError, state.TaskConflict):
                current = await state.lease_task(self.pool, lease)
                if current["status"] == "pausing":
                    await state.finish_task(self.pool, lease, "paused")
                    return
                await state.finish_task(self.pool, lease, "failed", stop_reason="INVALID_REPAIR_PLAN")
                return

    async def _run_graph(self, lease, task, plan):
        from shared.checkpoint_storage import PostgresCheckpointStorage

        gate = asyncio.Semaphore(self.concurrency)
        layer_count = len(plan.layers())
        stopped = []

        async def execute(step):
            async with gate:
                current = await state.lease_task(self.pool, lease)
                if current["status"] == "pausing":
                    return StepOutcome(status="blocked", error_code="PAUSED", source=current["execution_mode"])
                if stopped:
                    return StepOutcome(status="blocked", error_code=stopped[0], source="runtime")
                outcome = await self._execute_step(lease, current, step)
                if outcome.error_code in _POLICY_STOPS | _BUDGET_STOPS:
                    stopped.append(outcome.error_code)
                return outcome

        async def barrier(layer, signals):
            current = await state.lease_task(self.pool, lease)
            if current["status"] == "pausing":
                return "paused"
            if any(signal.status != "succeeded" for signal in signals):
                return "needs_replan"
            return "completed" if layer == layer_count - 1 else "continue"

        name = f"procurement:{task['id']}:spec{task['spec_revision']}:plan{task['plan_revision']}"
        if task["latest_checkpoint_id"] and task["checkpoint_plan_hash"] != task["plan_hash"]:
            raise state.TaskConflict("CHECKPOINT_PLAN_MISMATCH")

        async def saved(checkpoint_id, layer):
            await state.save_checkpoint(self.pool, lease, task["plan_hash"], checkpoint_id, layer)

        storage = PhaseCheckpointStorage(
            PostgresCheckpointStorage(self.pool),
            workflow_name=name,
            on_safe_checkpoint=saved,
            last_layer=task["checkpoint_layer"],
        )
        workflow = compile_plan(
            plan,
            name=name,
            execute_step=execute,
            decide_barrier=barrier,
            checkpoint_storage=storage,
        )
        # A final-stage checkpoint has no pending messages. Durable complete receipts
        # are enough to re-run only the final oracle after a process restart.
        if task["steps"] and all(
            row["status"] == "succeeded"
            and row["valid_until"]
            and datetime.fromisoformat(row["valid_until"]).timestamp() > time.time()
            and not row["reuse_invalidated"]
            for row in task["steps"]
        ):
            return RunSignal("completed", layer_count - 1)
        kwargs = (
            {"checkpoint_id": task["latest_checkpoint_id"]}
            if task["latest_checkpoint_id"]
            else {"message": LayerToken(0)}
        )
        result = None
        async for event in workflow.run(stream=True, **kwargs):
            if getattr(event, "type", None) == "output" and isinstance(getattr(event, "data", None), RunSignal):
                result = event.data
        return result or RunSignal("needs_replan", -1)

    async def _execute_step(self, lease, task, step):
        from shared.context import current_user_role

        revision = task["plan_revision"]
        existing = next(row for row in task["steps"] if row["id"] == step.id)
        if existing["status"] in {"failed", "blocked"} and existing["outcome"]:
            previous = StepOutcome.model_validate(existing["outcome"])
            if not _temporary(previous) or existing["attempt"] >= 2:
                return previous
        dependencies = {
            key: StepOutcome.model_validate(task["results"][key]) for key in step.depends_on if key in task["results"]
        }
        if len(dependencies) != len(step.depends_on) or any(x.status != "succeeded" for x in dependencies.values()):
            outcome = StepOutcome(status="blocked", error_code="DEPENDENCY_NOT_VERIFIED", source="runtime")
            return await state.record_unstarted_outcome(self.pool, lease, revision, step.id, outcome)
        by_id = {row["id"]: row for row in task["steps"]}
        if any(
            by_id[key]["reuse_invalidated"]
            or not by_id[key]["valid_until"]
            or datetime.fromisoformat(by_id[key]["valid_until"]).timestamp() <= time.time()
            for key in step.depends_on
        ):
            outcome = StepOutcome(status="blocked", error_code="DEPENDENCY_EXPIRED", source="runtime")
            return await state.record_unstarted_outcome(self.pool, lease, revision, step.id, outcome)
        try:
            raw_key = (
                await _call(self.reuse_key, task, step, dependencies)
                if self.reuse_key
                else {
                    "capability": step.capability,
                    "parameters": step.parameters,
                    "requirements": task["requirements"],
                    "dependencies": {key: value.model_dump(mode="json") for key, value in dependencies.items()},
                }
            )
        except Exception as exc:
            outcome = StepOutcome(
                status="failed",
                error_code=getattr(exc, "code", "INPUT_RESOLUTION_FAILED"),
                message=str(exc),
                source="runtime",
            )
            return await state.record_unstarted_outcome(self.pool, lease, revision, step.id, outcome)
        digest = canonical_hash(raw_key)
        cached = await state.find_reusable(self.pool, lease, step.capability, digest)
        if cached:
            if cached["plan_revision"] == revision and cached["step_id"] == step.id and step.id in task["results"]:
                outcome = StepOutcome.model_validate(cached["outcome"])
            else:
                outcome = await state.mark_reused(self.pool, lease, revision, step.id, digest, cached)
            await self._hook("after_capability", task, {"step": step, "outcome": outcome, "reused": True})
            if step.capability == "search_candidates":
                await self._hook("after_discovery", task, {"step": step, "outcome": outcome, "reused": True})
            return outcome
        while True:
            current = await state.lease_task(self.pool, lease)
            if current["status"] == "pausing":
                return StepOutcome(status="blocked", error_code="PAUSED", source="runtime")
            try:
                attempt_id, number = await state.begin_step(self.pool, lease, revision, step.id, digest)
            except state.TaskConflict as exc:
                if str(exc) == "PAUSE_REQUESTED":
                    return StepOutcome(status="blocked", error_code="PAUSED", source="runtime")
                raise
            role_token = current_user_role.set(current["owner_role"])
            try:
                try:
                    outcome = StepOutcome.model_validate(await _call(self.dispatcher, current, step, dependencies))
                except (state.BudgetExceeded, BudgetError) as exc:
                    outcome = StepOutcome(
                        status="blocked",
                        error_code="MODEL_CALL_LIMIT",
                        message=str(exc),
                        source=task["execution_mode"],
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    outcome = StepOutcome(
                        status="unknown",
                        error_code="DISPATCH_FAILED",
                        message=type(exc).__name__,
                        retryable=False,
                        source=task["execution_mode"],
                    )
            finally:
                current_user_role.reset(role_token)
            saved = await state.finish_step(self.pool, lease, revision, step.id, attempt_id, outcome)
            if saved is None:
                raise state.LeaseLost("晚到步骤结果已归档，没有覆盖新执行")
            await self._hook("after_capability", current, {"step": step, "outcome": saved, "reused": False})
            if step.capability == "search_candidates":
                await self._hook("after_discovery", current, {"step": step, "outcome": saved, "reused": False})
            if not (_temporary(saved) and number < 2):
                return saved
