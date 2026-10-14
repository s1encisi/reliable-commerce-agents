"""Use the installed MAF graph runner, not an alternate gather implementation."""

import asyncio

import pytest

from shared.procurement.models import LayerToken, Plan, PlanStep, ProcurementSpec, StepOutcome, plan_hash
from shared.procurement.workflow import compile_plan


def graph_plan(query="earphones"):
    return Plan(
        source="deterministic_demo",
        steps=[
            PlanStep(id="find", capability="search_candidates", parameters={"query": query}),
            PlanStep(id="price", capability="quote_options", depends_on=["find"]),
            PlanStep(id="stock", capability="check_inventory", depends_on=["find"]),
            PlanStep(id="reviews", capability="review_evidence", depends_on=["find"]),
        ],
    )


async def test_maf_parallel_layer_and_failure_envelope_reach_barrier():
    started = set()
    release = asyncio.Event()
    seen = []

    async def execute(step):
        if step.id != "find":
            started.add(step.id)
            if len(started) == 3:
                release.set()
            await asyncio.wait_for(release.wait(), 1)
        return StepOutcome(status="failed" if step.id == "price" else "succeeded", data={"id": step.id})

    async def barrier(index, signals):
        seen.append((index, {signal.step_id for signal in signals}))
        return "continue" if index == 0 else "needs_replan"

    workflow = compile_plan(graph_plan(), name="unit-frozen", execute_step=execute, decide_barrier=barrier)
    events = [event async for event in workflow.run(LayerToken(0), stream=True)]
    assert started == {"price", "stock", "reviews"}
    assert seen == [(0, {"find"}), (1, started)]
    outputs = [event.data for event in events if event.type == "output"]
    assert outputs[-1].status == "needs_replan"


def test_business_hash_covers_inputs_the_sdk_topology_hash_does_not():
    async def execute(step):
        return StepOutcome(status="succeeded")

    async def barrier(index, signals):
        return "completed"

    first, second = graph_plan("a"), graph_plan("b")
    a = compile_plan(first, name="same", execute_step=execute, decide_barrier=barrier)
    b = compile_plan(second, name="same", execute_step=execute, decide_barrier=barrier)
    spec = ProcurementSpec(query="耳机", quantity=1, total_budget="500")
    assert a.graph_signature_hash == b.graph_signature_hash
    assert plan_hash(first, spec) != plan_hash(second, spec)
    with pytest.raises(ValueError):
        Plan(steps=[PlanStep(id="loop", capability="search_candidates", depends_on=["loop"])])
