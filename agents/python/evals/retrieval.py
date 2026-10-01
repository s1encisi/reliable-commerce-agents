"""真实隔离 PostgreSQL 上的检索工程评测；离线向量不代表语义模型质量。

python -m evals.retrieval --output <new-report.json>
只创建独立 testcontainers 数据库，不接受外部 DSN，不调用模型。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[3]
DATASET = Path(__file__).with_name("retrieval_data") / "zh.json"
VARIANTS = ("lexical_only_ablation", "hybrid_replay", "embedding_unavailable")
SOURCE_PATHS = (
    "agents/python/evals/retrieval.py",
    "agents/python/evals/retrieval_data/zh.json",
    "agents/python/product_discovery/tools.py",
    "agents/python/shared/search.py",
    "agents/python/shared/replay_embeddings.py",
    "agents/python/uv.lock",
    "docker/postgres/init.sql",
)


def load_dataset(path: Path = DATASET) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    keys = {row["key"] for row in data["catalog"]}
    if len(keys) != len(data["catalog"]):
        raise ValueError("Catalog keys must be unique")
    case_ids = {case["id"] for case in data["cases"]}
    if len(case_ids) != len(data["cases"]):
        raise ValueError("Case IDs must be unique")
    for case in data["cases"]:
        if not 1 <= case["k"] <= 100 or not set(case["relevance"]) <= keys:
            raise ValueError("Invalid retrieval judgment")
        if any(type(grade) is not int or grade < 1 for grade in case["relevance"].values()):
            raise ValueError("Relevance grades must be positive integers")
    return data


def entity_id(key: str):
    return uuid5(NAMESPACE_URL, f"reliable-commerce-agents:retrieval-v1:{key}")


def source_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((ROOT / name).read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        for name in SOURCE_PATHS
    }


def ranking_metrics(actual: list[str], relevance: dict[str, int], k: int) -> dict:
    actual = actual[:k]
    if not relevance:
        return {
            "recall_at_k": None,
            "ndcg_at_k": None,
            "precision_at_k": 0.0,
            "precision_returned": 0.0,
            "no_result_correct": len(actual) == 0,
        }
    # 重复结果不重复获得相关性分数。
    seen = set()
    grades = []
    for item in actual:
        grades.append(relevance.get(item, 0) if item not in seen else 0)
        seen.add(item)
    dcg = sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(ideal))
    return {
        "recall_at_k": len(set(actual) & set(relevance)) / len(relevance),
        "precision_at_k": len(set(actual) & set(relevance)) / k,
        "precision_returned": len(set(actual) & set(relevance)) / len(actual) if actual else 0.0,
        "ndcg_at_k": dcg / idcg,
        "no_result_correct": None,
    }


def constraint_violations(rows: list[dict], filters: dict, catalog: dict) -> list[dict]:
    violations = []
    categories = {"电子产品": "Electronics", "服装": "Clothing", "家居": "Home", "运动": "Sports", "图书": "Books"}
    for row in rows:
        reasons = []
        known = catalog.get(row["id"])
        if known is None:
            reasons.append("unknown_product")
        elif not known.get("is_active", True):
            reasons.append("inactive_product")
        category = categories.get(filters.get("category"), filters.get("category"))
        if category and row["category"] != category:
            reasons.append("category")
        if filters.get("min_price") is not None and row["price"] < filters["min_price"]:
            reasons.append("min_price")
        if filters.get("max_price") is not None and row["price"] > filters["max_price"]:
            reasons.append("max_price")
        if filters.get("min_rating") is not None and row["rating"] < filters["min_rating"]:
            reasons.append("min_rating")
        if reasons:
            violations.append({"id": row["id"], "reasons": reasons})
    return violations


async def seed_catalog(pool, dataset: dict) -> None:
    from shared.replay_embeddings import embed_text

    async with pool.acquire() as conn:
        if await conn.fetchval("SELECT COUNT(*) FROM products"):
            raise RuntimeError("Evaluation requires an empty dedicated catalog; existing data was not changed")
        async with conn.transaction():
            for row in dataset["catalog"]:
                pid = entity_id(row["key"])
                await conn.execute(
                    "INSERT INTO products(id,name,description,category,brand,price,rating,is_active) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
                    pid,
                    row["name"],
                    row["description"],
                    row["category"],
                    row["brand"],
                    row["price"],
                    row["rating"],
                    row.get("is_active", True),
                )
                vector = embed_text(" ".join((row["name"], row["brand"], row["description"])))
                await conn.execute(
                    "INSERT INTO product_embeddings(product_id,embedding) VALUES($1,$2::vector)",
                    pid,
                    json.dumps(vector),
                )
            await conn.execute("REINDEX INDEX idx_product_embedding")
            await conn.execute("ANALYZE products")


def summarize(cases: list[dict]) -> dict:
    recall = [case["metrics"]["recall_at_k"] for case in cases if case["metrics"]["recall_at_k"] is not None]
    ndcg = [case["metrics"]["ndcg_at_k"] for case in cases if case["metrics"]["ndcg_at_k"] is not None]
    precision = [case["metrics"]["precision_at_k"] for case in cases if case["metrics"]["recall_at_k"] is not None]
    empty = [case["metrics"]["no_result_correct"] for case in cases if case["metrics"]["no_result_correct"] is not None]
    durations = sorted(ms for case in cases for ms in case["latency_ms"])
    return {
        "case_count": len(cases),
        "relevant_case_count": len(recall),
        "mean_recall_at_k": statistics.mean(recall) if recall else None,
        "mean_ndcg_at_k": statistics.mean(ndcg) if ndcg else None,
        "mean_precision_at_k": statistics.mean(precision) if precision else None,
        "expected_empty_cases": len(empty),
        "correct_empty_cases": sum(empty),
        "constraint_violation_count": sum(len(case["constraint_violations"]) for case in cases),
        "mode_contract_error_count": sum(bool(case["mode_contract_errors"]) for case in cases),
        "latency_ms_median": statistics.median(durations),
        "latency_ms_p95_nearest_rank": durations[math.ceil(0.95 * len(durations)) - 1],
    }


async def run_benchmark(pool, dataset: dict, *, repetitions: int = 3, warmups: int = 1) -> dict:
    from product_discovery import tools
    from shared.factory import EmbeddingsUnavailableError
    from shared.replay_embeddings import ReplayEmbeddingsClient

    if repetitions < 1 or warmups < 0:
        raise ValueError("Invalid repetition counts")
    catalog = {str(entity_id(row["key"])): row for row in dataset["catalog"]}
    output = {}

    def unavailable():
        raise EmbeddingsUnavailableError("Explicit evaluation ablation: no embedding provider")

    with (
        patch.object(tools, "get_pool", return_value=pool),
        patch.object(tools, "get_embedding_model", return_value="offline-feature-hash-v1"),
    ):
        for variant in VARIANTS:
            factory = unavailable if variant == "embedding_unavailable" else ReplayEmbeddingsClient
            cases = []
            with patch.object(tools, "create_embedding_client", side_effect=factory):
                for case in dataset["cases"]:
                    call = (
                        tools.search_products.func if variant == "lexical_only_ablation" else tools.semantic_search.func
                    )
                    durations = []
                    returned_runs = []
                    rows = []
                    for iteration in range(warmups + repetitions):
                        start = time.perf_counter_ns()
                        rows = await call(query=case["query"], limit=case["k"], **case["filters"])
                        elapsed = (time.perf_counter_ns() - start) / 1_000_000
                        if iteration >= warmups:
                            durations.append(elapsed)
                            returned_runs.append([row["id"] for row in rows])
                    actual = [row["id"] for row in rows]
                    relevant = {str(entity_id(key)): grade for key, grade in case["relevance"].items()}
                    mode_errors = []
                    if variant == "embedding_unavailable" and any(
                        row.get("retrieval_mode") != "lexical" or row.get("embedding_available") is not False
                        for row in rows
                    ):
                        mode_errors.append("fallback_not_explicitly_labeled")
                    if variant == "hybrid_replay" and any(
                        row.get("retrieval_mode") != "hybrid" or row.get("embedding_available") is not True
                        for row in rows
                    ):
                        mode_errors.append("hybrid_not_explicitly_labeled")
                    cases.append(
                        {
                            "id": case["id"],
                            "query": case["query"],
                            "tags": case["tags"],
                            "filters": case["filters"],
                            "k": case["k"],
                            "expected_relevance": relevant,
                            "returned_ids": actual,
                            "returned_keys": [catalog[row_id]["key"] for row_id in actual],
                            "metrics": ranking_metrics(actual, relevant, case["k"]),
                            "constraint_violations": constraint_violations(rows, case["filters"], catalog),
                            "mode_contract_errors": mode_errors,
                            "latency_ms": durations,
                            "latency_ms_median": statistics.median(durations),
                            "ranking_stable_across_repetitions": all(run == actual for run in returned_runs),
                        }
                    )
            output[variant] = {"summary": summarize(cases), "cases": cases}
    return output


def _offline_environment() -> None:
    os.environ["APP_ENV_FILE"] = "/dev/null"
    os.environ["RECORD"] = "false"
    os.environ["LLM_PROVIDER"] = "replay"
    os.environ["OTEL_ENABLED"] = "false"
    for key in (
        "OPENAI_API_KEY",
        "AZURE_OPENAI_KEY",
        "AZURE_OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "TYPESAFE_API_KEY",
    ):
        os.environ[key] = ""


async def evaluate_database(dsn: str, dataset: dict, *, repetitions: int, warmups: int) -> tuple[dict, dict]:
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            await conn.execute((ROOT / "docker/postgres/init.sql").read_text(encoding="utf-8"))
            database = {
                "postgres": await conn.fetchval("SELECT version()"),
                "pgvector": await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname='vector'"),
            }
        await seed_catalog(pool, dataset)
        result = await run_benchmark(pool, dataset, repetitions=repetitions, warmups=warmups)
        return result, database
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/evaluation/retrieval-20261001.json")
    parser.add_argument("--repetitions", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--warmups", type=int, choices=range(0, 4), default=1)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Existing report preserved; choose a new --output path")
    _offline_environment()
    from testcontainers.community.postgres import PostgresContainer

    dataset = load_dataset()
    hashes = source_hashes()
    started = datetime.now(UTC).isoformat()
    with PostgresContainer("pgvector/pgvector:pg16", dbname="isolated_retrieval_eval") as container:
        dsn = container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        variants, database = asyncio.run(
            evaluate_database(dsn, dataset, repetitions=args.repetitions, warmups=args.warmups)
        )
    if hashes != source_hashes():
        raise RuntimeError("Evaluation source changed during this run; no report was saved")
    report = {
        "scope": "Retrieval engineering evaluation with real isolated PostgreSQL; no LLM or paid embedding",
        "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "version": dataset["version"],
            "provenance": dataset["provenance"],
            "human_review_status": dataset["human_review_status"],
        },
        "embedding": {
            "implementation": "shared.replay_embeddings.embed_text",
            "scope": dataset["embedding_scope"],
            "dimensions": 1536,
        },
        "comparison": {
            "baseline": "lexical_only_ablation is current search_products with identical filters",
            "not_a_historical_before_after": True,
            "variants": list(VARIANTS),
        },
        "protocol": {
            "repetitions": args.repetitions,
            "warmups_per_case_variant": args.warmups,
            "case_order": "dataset order; variants in fixed order",
            "repetitions_are_independent_samples": False,
            "empty_relevance_metrics": "Recall and nDCG are null; score no_result_correct instead",
            "precision_denominator": "precision_at_k uses requested k; precision_returned uses actual result count",
            "latency_scope": (
                "Sequential local synthetic calls including offline embedding; not production capacity or SLA"
            ),
            "source_hash_format": "SHA-256 of UTF-8 source with LF-normalized newlines",
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "database": database,
            "container_image": "pgvector/pgvector:pg16",
            "packages": {name: importlib.metadata.version(name) for name in ("asyncpg", "testcontainers", "pgvector")},
        },
        "git": {
            "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
            "working_tree_has_changes": bool(
                subprocess.check_output(
                    ["git", "status", "--porcelain"],
                    cwd=ROOT,
                    text=True,
                ).strip()
            ),
        },
        "implementation_sha256": hashes,
        "variants": variants,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for variant, value in variants.items():
        print(f"{variant}: {json.dumps(value['summary'], ensure_ascii=False)}")
    print(f"Saved report: {args.output}")


if __name__ == "__main__":
    main()
