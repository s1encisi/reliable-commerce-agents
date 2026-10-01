"""检索评测器的计分与真实隔离数据库运行测试，不访问外部模型。"""

import json
import math

import pytest

from evals.retrieval import (
    constraint_violations,
    entity_id,
    load_dataset,
    ranking_metrics,
    run_benchmark,
    seed_catalog,
)


def test_ranking_metrics_reward_order_and_do_not_reward_duplicates():
    relevance = {"a": 3, "b": 1}
    assert ranking_metrics(["a", "b"], relevance, 2)["ndcg_at_k"] == 1
    reversed_score = ranking_metrics(["b", "a"], relevance, 2)
    assert reversed_score["recall_at_k"] == 1
    assert reversed_score["ndcg_at_k"] == pytest.approx((1 + 7 / math.log2(3)) / (7 + 1 / math.log2(3)))
    duplicate = ranking_metrics(["a", "a"], relevance, 2)
    assert duplicate["recall_at_k"] == 0.5
    assert duplicate["precision_at_k"] == 0.5
    assert duplicate["ndcg_at_k"] < 1
    assert ranking_metrics(["irrelevant"], relevance, 2)["recall_at_k"] == 0


def test_empty_judgments_use_abstention_instead_of_inventing_recall():
    empty = ranking_metrics([], {}, 3)
    assert empty["recall_at_k"] is None and empty["ndcg_at_k"] is None
    assert empty["no_result_correct"] is True
    assert ranking_metrics(["irrelevant"], {}, 3)["no_result_correct"] is False


def test_constraint_oracle_is_independent_of_ranking():
    rows = [{"id": "a", "category": "Home", "price": 500, "rating": 2}]
    catalog = {"a": {"is_active": False}}
    violations = constraint_violations(rows, {"category": "电子产品", "max_price": 100, "min_rating": 4}, catalog)
    assert set(violations[0]["reasons"]) == {"inactive_product", "category", "max_price", "min_rating"}


def test_dataset_requires_explicit_provenance_and_valid_judgments(tmp_path):
    dataset = load_dataset()
    assert dataset["human_review_status"] == "pending_user_review"
    assert len(dataset["cases"]) == 12
    bad = tmp_path / "bad.json"
    dataset["cases"][0]["relevance"] = {"nonexistent": 2}
    bad.write_text(json.dumps(dataset))
    with pytest.raises(ValueError, match="judgment"):
        load_dataset(bad)


async def test_benchmark_executes_all_variants_on_real_postgres(clean_db):
    dataset = load_dataset()
    await seed_catalog(clean_db, dataset)
    report = await run_benchmark(clean_db, dataset, repetitions=1, warmups=0)
    assert set(report) == {"lexical_only_ablation", "hybrid_replay", "embedding_unavailable"}
    for variant in report.values():
        assert variant["summary"]["case_count"] == 12
        assert variant["summary"]["constraint_violation_count"] == 0
        assert variant["summary"]["mode_contract_error_count"] == 0
        assert variant["summary"]["expected_empty_cases"] == 3
        assert all(len(case["latency_ms"]) == 1 for case in variant["cases"])
    lexical = {case["id"]: case for case in report["lexical_only_ablation"]["cases"]}
    fallback = {case["id"]: case for case in report["embedding_unavailable"]["cases"]}
    assert all(lexical[key]["returned_ids"] == fallback[key]["returned_ids"] for key in lexical)
    assert lexical["exact_model"]["returned_ids"][0] == str(entity_id("sony_xm5"))
    assert lexical["exact_model"]["metrics"]["recall_at_k"] == 1
    for variant in report.values():
        impossible = next(case for case in variant["cases"] if case["id"] == "impossible_budget")
        assert impossible["returned_ids"] == [] and impossible["metrics"]["no_result_correct"] is True
    assert json.loads(json.dumps(report))["hybrid_replay"]["summary"]["case_count"] == 12


async def test_seeder_refuses_existing_catalog_instead_of_resetting_it(clean_db):
    dataset = load_dataset()
    await seed_catalog(clean_db, dataset)
    with pytest.raises(RuntimeError, match="existing data was not changed"):
        await seed_catalog(clean_db, dataset)
    assert await clean_db.fetchval("SELECT COUNT(*) FROM products") == len(dataset["catalog"])
