"""Compile a frozen plan into MAF topological layers with complete-wave barriers."""

from collections.abc import Awaitable, Callable

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler
from agent_framework._workflows._checkpoint import CheckpointStorage, WorkflowCheckpointException

from shared.procurement.models import LayerToken, Plan, RunSignal, StepOutcome, StepSignal


class _Start(Executor):
    def __init__(self):
        super().__init__(id="procurement_start")

    @handler
    async def run(self, token: LayerToken, ctx: WorkflowContext[LayerToken]) -> None:
        await ctx.send_message(token)


class _Step(Executor):
    def __init__(self, step, execute: Callable[..., Awaitable[StepOutcome]]):
        super().__init__(id="step_" + step.id)
        self.step = step
        self._execute_step_callback = execute

    @handler
    async def run(self, token: LayerToken, ctx: WorkflowContext[StepSignal]) -> None:
        outcome = await self._execute_step_callback(self.step)
        await ctx.send_message(StepSignal(self.step.id, outcome.status))


class _Barrier(Executor):
    def __init__(self, layer: int, decide):
        super().__init__(id=f"barrier_{layer}")
        self.layer = layer
        self.decide = decide
        self.completed = False

    async def _finish(self, signals, ctx):
        status = await self.decide(self.layer, signals)
        # Paused/failed waves may contain nodes never dispatched. Do not publish
        # their checkpoint as a complete wave; receipts permit replay without new calls.
        self.completed = status in {"continue", "completed"}
        if status == "continue":
            await ctx.send_message(LayerToken(self.layer + 1))
        else:
            await ctx.yield_output(RunSignal(status, self.layer))

    @handler
    async def one(self, signal: StepSignal, ctx: WorkflowContext[LayerToken, RunSignal]) -> None:
        await self._finish([signal], ctx)

    @handler
    async def many(self, signals: list[StepSignal], ctx: WorkflowContext[LayerToken, RunSignal]) -> None:
        await self._finish(signals, ctx)

    async def on_checkpoint_save(self) -> dict:
        return {"layer": self.layer, "completed": self.completed}

    async def on_checkpoint_restore(self, state: dict) -> None:
        self.completed = bool(state.get("completed", False))


def compile_plan(plan: Plan, *, name: str, execute_step, decide_barrier, checkpoint_storage=None):
    """No cross-depth native fan-in: dependencies are read from durable step receipts."""
    start = _Start()
    layers = plan.layers()
    barriers = [_Barrier(index, decide_barrier) for index in range(len(layers))]
    builder = WorkflowBuilder(
        start_executor=start,
        name=name,
        max_iterations=20,
        checkpoint_storage=checkpoint_storage,
        output_from=barriers,
    )
    previous = start
    for index, layer in enumerate(layers):
        nodes = [_Step(step, execute_step) for step in layer]
        if len(nodes) == 1:
            builder.add_edge(previous, nodes[0])
            builder.add_edge(nodes[0], barriers[index])
        else:
            builder.add_fan_out_edges(previous, nodes)
            builder.add_fan_in_edges(nodes, barriers[index])
        previous = barriers[index]
    return builder.build()


class PhaseCheckpointStorage(CheckpointStorage):
    """Persist SDK checkpoints; expose only completed-wave points to application recovery."""

    def __init__(self, inner, *, workflow_name: str, on_safe_checkpoint, last_layer=-1):
        self.inner = inner
        self.workflow_name = workflow_name
        self.on_safe_checkpoint = on_safe_checkpoint
        self.last_layer = last_layer

    async def save(self, checkpoint):
        checkpoint_id = await self.inner.save(checkpoint)
        saved = checkpoint.state.get("_executor_state", {})
        complete = [
            value["layer"]
            for key, value in saved.items()
            if key.startswith("barrier_") and isinstance(value, dict) and value.get("completed")
        ]
        layer = max(complete, default=-1)
        if layer > self.last_layer:
            await self.on_safe_checkpoint(checkpoint_id, layer)
            self.last_layer = layer
        return checkpoint_id

    async def load(self, checkpoint_id):
        checkpoint = await self.inner.load(checkpoint_id)
        if checkpoint.workflow_name != self.workflow_name:
            raise WorkflowCheckpointException("Checkpoint belongs to a different task or plan revision")
        return checkpoint

    async def list_checkpoints(self, *, workflow_name):
        return await self.inner.list_checkpoints(workflow_name=workflow_name)

    async def list_checkpoint_ids(self, *, workflow_name):
        return await self.inner.list_checkpoint_ids(workflow_name=workflow_name)

    async def get_latest(self, *, workflow_name):
        return await self.inner.get_latest(workflow_name=workflow_name)

    async def delete(self, checkpoint_id):
        return await self.inner.delete(checkpoint_id)
