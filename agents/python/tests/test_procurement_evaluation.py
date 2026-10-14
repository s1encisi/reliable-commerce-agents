"""采购评测器自身的隔离数据库校验，不接受外部DSN。"""

from copy import deepcopy
from pathlib import Path

import asyncpg
import pytest

from evals.procurement import (
    DB_NAME,
    OWNER,
    LogicalEvents,
    load_dataset,
    reference_check,
    run_trial,
    seed_case,
    summarize,
)
from shared.procurement.domain import execute_capability


@pytest.fixture
async def evaluation_pool(database_url):
    admin = await asyncpg.connect(database_url)
    # database_url comes only from the session's new testcontainer.
    await admin.execute(f"CREATE DATABASE {DB_NAME}")
    pool = await asyncpg.create_pool(database_url.rsplit("/", 1)[0] + "/" + DB_NAME, max_size=12)
    try:
        await pool.execute((Path(__file__).resolve().parents[3] / "docker/postgres/init.sql").read_text())
        yield pool
    finally:
        await pool.close()
        await admin.execute(f"DROP DATABASE {DB_NAME}")
        await admin.close()


def test_dataset_has_fixed_independent_splits_and_truth():
    data = load_dataset()
    assert len(data["cases"]) == 30
    assert sum(case["split"] == "dev" for case in data["cases"]) == 18
    assert sum(not case["expected"]["feasible"] for case in data["cases"]) == 4
    assert data["human_review_status"] == "pending_user_review"
    assert all(case["spec"]["query"] == "task" + case["id"] for case in data["cases"])


async def test_case_seed_refuses_shared_or_wrong_database(clean_db):
    before = await clean_db.fetchval("SELECT count(*) FROM products")
    with pytest.raises(RuntimeError, match="隔离库"):
        await seed_case(clean_db, load_dataset()["cases"][0])
    assert await clean_db.fetchval("SELECT count(*) FROM products") == before


async def test_reference_accepts_multiple_valid_options_and_rejects_forgery(evaluation_pool):
    pool = evaluation_pool
    case = load_dataset()["cases"][1]
    ids = await seed_case(pool, case)
    options = []
    for key in ("c", "b"):
        product = next(product for product in case["catalog"] if product["key"] == key)
        option = {"product_id": ids[key], "name": product["name"], "quantity": case["spec"]["quantity"]}
        for capability, field in (("quote_options", "quote"), ("review_evidence", "review")):
            data = await execute_capability(pool, capability, case["spec"], {"product_ids": [ids[key]]}, OWNER)
            option[field] = data["items"][0]
        options.append(option)
    assert (await reference_check(pool, case, ids, options))["passed"]
    tampered = deepcopy(options)
    tampered[0]["quote"]["final_total"] = "0.01"
    assert not (await reference_check(pool, case, ids, tampered))["passed"]
    tampered = deepcopy(options)
    tampered[0]["review"]["reviews"][0]["body"] = "伪造好评"
    assert not (await reference_check(pool, case, ids, tampered))["passed"]


async def test_logical_event_runs_once_at_exact_boundary(evaluation_pool):
    case = next(case for case in load_dataset()["cases"] if case["id"] == "d15")
    ids = await seed_case(evaluation_pool, case)
    hook = LogicalEvents(evaluation_pool, case, ids)
    await hook("after_discovery", {}, {})
    assert hook.fired == []
    await hook("before_finalize", {}, {})
    assert hook.fired == [0]
    assert await evaluation_pool.fetchval("SELECT quantity FROM warehouse_inventory WHERE product_id=$1", ids["a"]) == 0
    await hook("before_finalize", {}, {})
    assert hook.fired == [0]


@pytest.mark.parametrize("variant", ["sequential_adapter", "maf", "no_parallel", "no_local_repair"])
async def test_real_runner_variants_keep_all_trials_and_safe_stops(evaluation_pool, variant):
    case = next(case for case in load_dataset()["cases"] if case["id"] == "d15")
    trial = await run_trial(evaluation_pool, case, variant)
    assert trial["error"] is None, trial
    assert trial["bounded"] and not trial["safety_violation"]
    assert trial["fired_event_indexes"] == [0]
    assert trial["task_success"] == (variant != "no_local_repair")
    if variant in {"maf", "no_parallel"}:
        assert trial["counters"]["reused_steps"] >= 3
    assert trial["peak_domain_calls"] <= (1 if variant in {"sequential_adapter", "no_parallel"} else 2)
    summary = summarize([trial])[variant]
    assert summary["trials"] == 1
    assert summary["task_success"] == int(trial["task_success"])


async def test_second_logical_failure_stops_after_one_repair(evaluation_pool):
    case = deepcopy(next(case for case in load_dataset()["cases"] if case["id"] == "d15"))
    case["events"].append({"at": "before_finalize", "occurrence": 2, "product": "b", "action": "stock", "value": 0})
    case["expected"] = {"feasible": False, "acceptable_product_keys": ["c"]}
    trial = await run_trial(evaluation_pool, case, "maf")
    assert trial["error"] is None, trial
    assert trial["fired_event_indexes"] == [0, 1]
    assert trial["safe_noncompletion"] and not trial["task_success"]
    assert trial["counters"]["repairs_used"] == 1 and trial["bounded"]


async def test_harness_failure_keeps_all_planned_trials(monkeypatch):
    from evals import procurement

    async def unavailable(*args, **kwargs):
        raise ConnectionError("synthetic disconnected evaluation database")

    monkeypatch.setattr(procurement, "run_trial", unavailable)
    trials = await procurement.evaluate(None, load_dataset())
    assert len(trials) == 120
    assert all(row["status"] == "harness_error" and not row["metrics_complete"] for row in trials)
    groups = summarize(trials)
    assert all(group["exceptions"] == 30 and group["p50_ms"] is None for group in groups.values())
