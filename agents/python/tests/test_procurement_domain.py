"""真实隔离 PostgreSQL 验证采购边界；不访问开发数据库或模型。"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from shared.procurement.domain import DomainError, execute_capability, verify_options

OWNER = "procurement@example.test"
OTHER = "other@example.test"


@pytest.fixture
async def catalog(clean_db):
    pool = clean_db
    owner = uuid4()
    await pool.execute(
        "INSERT INTO users(id,email,password_hash,name) VALUES($1,$2,'unused','合成采购用户')", owner, OWNER
    )
    await pool.execute("INSERT INTO users(email,password_hash,name) VALUES($1,'unused','其他用户')", OTHER)
    warehouse = uuid4()
    await pool.execute(
        "INSERT INTO warehouses(id,name,location,region) VALUES($1,'采购合成仓','本地','east')", warehouse
    )
    pids = [uuid4(), uuid4(), uuid4()]
    for index, pid in enumerate(pids):
        await pool.execute(
            """INSERT INTO products(id,name,description,category,price,rating)
               VALUES($1,$2,'合成采购证据，非真实销售','Electronics',$3,4.5)""",
            pid,
            f"采购耳机 headphones {index}",
            Decimal(100 + index * 20),
        )
        await pool.execute(
            "INSERT INTO warehouse_inventory(warehouse_id,product_id,quantity) VALUES($1,$2,10)",
            warehouse,
            pid,
        )
        await pool.execute(
            "INSERT INTO reviews(product_id,user_id,rating,title,body) VALUES($1,$2,2,'原文','佩戴两小时后不舒适。')",
            pid,
            owner,
        )
    return pool, [str(pid) for pid in pids]


def specification(**kwargs):
    return {
        "query": "耳机",
        "quantity": 2,
        "alternative_count": 1,
        "total_budget": "220.00",
        "currency": "CNY",
        "in_stock": True,
        "budget_scope": "goods",
        "quote_policy": "single_coupon_v1",
        "preferences": [],
        **kwargs,
    }


async def coupon(pool, **kwargs):
    data = {
        "discount_type": "fixed",
        "discount_value": Decimal("25"),
        "min_spend": Decimal("200"),
        "max_discount": None,
        "usage_limit": 10,
        "times_used": 0,
        "valid_from": datetime.now(UTC) - timedelta(days=1),
        "valid_until": datetime.now(UTC) + timedelta(days=1),
        "applicable_categories": ["Electronics"],
        "user_specific_email": OWNER,
        "is_active": True,
        **kwargs,
    }
    columns = ",".join(data)
    placeholders = ",".join(f"${index}" for index in range(3, len(data) + 3))
    cid = uuid4()
    await pool.execute(
        f"INSERT INTO coupons(id,code,{columns}) VALUES($1,$2,{placeholders})", cid, str(cid), *data.values()
    )
    return str(cid)


async def evidence_for(pool, ids, spec=None):
    spec = spec or specification(alternative_count=len(ids))
    discovery = await execute_capability(pool, "search_candidates", spec, {}, OWNER)
    evidence = [discovery]
    options = []
    names = {item["product_id"]: item["name"] for item in discovery["items"]}
    for pid in ids:
        option = {"product_id": pid, "name": names[pid], "quantity": spec["quantity"]}
        for capability, field in (
            ("quote_options", "quote"),
            ("check_inventory", "inventory"),
            ("review_evidence", "review"),
        ):
            result = await execute_capability(pool, capability, spec, {"product_ids": [pid]}, OWNER)
            evidence.append(result)
            option[field] = result["items"][0]
        options.append(option)
    return spec, options, evidence


@pytest.mark.parametrize("quantity", [0, 101, -1, 1.5, "2", True])
async def test_reject_invalid_quantity(catalog, quantity):
    pool, ids = catalog
    with pytest.raises(DomainError, match="数量"):
        await execute_capability(
            pool, "quote_options", specification(quantity=quantity), {"product_ids": ids[:1]}, OWNER
        )


async def test_fixed_percentage_best_single_coupon_and_no_mutation(catalog):
    pool, ids = catalog
    await coupon(pool)
    best = await coupon(pool, discount_type="percentage", discount_value=Decimal("20"), max_discount=Decimal("30"))
    before = await pool.fetchval("SELECT sum(quantity) FROM warehouse_inventory")
    result = await execute_capability(pool, "quote_options", specification(), {"product_ids": ids[:1]}, OWNER)
    item = result["items"][0]
    assert (item["original_total"], item["discount_amount"], item["final_total"]) == ("200.00", "30.00", "170.00")
    assert item["coupon_id"] == best
    assert len(item["rule_hash"]) == 64 and item["source_ids"]["coupons"] == [best]
    assert await pool.fetchval("SELECT sum(times_used) FROM coupons") == 0
    assert await pool.fetchval("SELECT sum(quantity) FROM warehouse_inventory") == before


@pytest.mark.parametrize(
    "change",
    [
        {"user_specific_email": OTHER},
        {"applicable_categories": ["Books"]},
        {"min_spend": Decimal("200.01")},
        {"valid_until": datetime.now(UTC) - timedelta(seconds=1)},
        {"valid_from": datetime.now(UTC) + timedelta(days=1)},
        {"usage_limit": 10, "times_used": 10},
        {"is_active": False},
        {"applicable_categories": []},
    ],
)
async def test_ineligible_coupon_not_used(catalog, change):
    pool, ids = catalog
    await coupon(pool, **change)
    result = await execute_capability(pool, "quote_options", specification(), {"product_ids": ids[:1]}, OWNER)
    assert result["items"][0]["coupon_id"] is None
    assert result["items"][0]["final_total"] == "200.00"


async def test_no_coupon_decimal_rounding_and_discount_capped_by_total(catalog):
    pool, ids = catalog
    await pool.execute("UPDATE products SET price=33.35 WHERE id=$1", ids[0])
    await coupon(pool, min_spend=Decimal("0"), discount_type="percentage", discount_value=Decimal("15"))
    result = await execute_capability(pool, "quote_options", specification(quantity=1), {"product_ids": ids[:1]}, OWNER)
    assert result["items"][0]["discount_amount"] == "5.00"
    await coupon(pool, min_spend=Decimal("0"), discount_value=Decimal("500"))
    result = await execute_capability(pool, "quote_options", specification(), {"product_ids": ids[:1]}, OWNER)
    assert result["items"][0]["final_total"] == "0.00"


async def test_two_skus_quoted_independently_not_a_basket(catalog):
    pool, ids = catalog
    await coupon(pool)
    result = await execute_capability(pool, "quote_options", specification(), {"product_ids": ids[:2]}, OWNER)
    assert [item["original_total"] for item in result["items"]] == ["200.00", "240.00"]
    assert [item["final_total"] for item in result["items"]] == ["175.00", "215.00"]
    assert len({item["rule_hash"] for item in result["items"]}) == 2


async def test_search_keeps_expensive_candidates_and_excludes_scope(catalog):
    pool, ids = catalog
    result = await execute_capability(
        pool, "search_candidates", specification(total_budget="1"), {"exclude_ids": [ids[1]]}, OWNER
    )
    assert {item["product_id"] for item in result["items"]} == {ids[0], ids[2]}
    with pytest.raises(DomainError):
        await execute_capability(pool, "search_candidates", specification(), {"candidate_limit": 7}, OWNER)


async def test_inventory_quantity_and_review_original_ids(catalog):
    pool, ids = catalog
    result = await execute_capability(
        pool, "check_inventory", specification(quantity=11), {"product_ids": ids[:1]}, OWNER
    )
    assert result["items"][0]["sufficient"] is False
    result = await execute_capability(pool, "review_evidence", specification(), {"product_ids": ids[:1]}, OWNER)
    item = result["items"][0]
    assert item["reviews"][0]["body"] == "佩戴两小时后不舒适。"
    assert item["risk_references"][0]["review_id"] == item["reviews"][0]["id"]
    assert item["risk_references"][0]["quote"] == item["reviews"][0]["body"]


async def test_oracle_passes_real_facts_and_preserves_independent_scopes(catalog):
    pool, ids = catalog
    await coupon(pool)
    spec, options, evidence = await evidence_for(pool, ids[:2])
    report = await verify_options(pool, spec, options, evidence, OWNER)
    assert report["passed"], report
    await pool.execute("UPDATE warehouse_inventory SET quantity=0 WHERE product_id=$1", ids[0])
    report = await verify_options(pool, spec, options, evidence, OWNER)
    assert not report["passed"] and report["affected_product_ids"] == ids[:1]
    assert "INSUFFICIENT_STOCK" in {failure["code"] for failure in report["failures"]}


@pytest.mark.parametrize("tamper", ["source", "expiry", "quantity", "price", "rule_hash", "review", "candidate"])
async def test_oracle_rejects_forged_or_stale_evidence(catalog, tamper):
    pool, ids = catalog
    spec, options, evidence = await evidence_for(pool, ids[:1])
    evidence = deepcopy(evidence)
    if tamper == "source":
        evidence[1]["source"] = "model:invented"
    elif tamper == "expiry":
        evidence[1]["valid_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    elif tamper == "quantity":
        options[0]["quantity"] = 3
    elif tamper in ("price", "rule_hash"):
        key = "final_total" if tamper == "price" else "rule_hash"
        options[0]["quote"][key] = "1.00" if tamper == "price" else "fabricated"
        evidence[1]["items"][0][key] = options[0]["quote"][key]
    elif tamper == "review":
        options[0]["review"]["reviews"][0]["body"] = "模型虚构的好评"
        evidence[3]["items"][0]["reviews"][0]["body"] = "模型虚构的好评"
    else:
        evidence[0]["items"] = []
    report = await verify_options(pool, spec, options, evidence, OWNER)
    assert not report["passed"], report


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("UPDATE products SET price=101 WHERE id=$1", "PRICE_CHANGED"),
        ("UPDATE products SET rating=2 WHERE id=$1", "RATING_TOO_LOW"),
        ("DELETE FROM reviews WHERE product_id=$1", "INSUFFICIENT_REVIEWS"),
        ("UPDATE products SET is_active=false WHERE id=$1", "UNKNOWN_PRODUCT"),
    ],
)
async def test_final_oracle_reloads_live_state(catalog, mutation, expected):
    pool, ids = catalog
    spec, options, evidence = await evidence_for(pool, ids[:1], specification(min_rating=4))
    await pool.execute(mutation, ids[0])
    report = await verify_options(pool, spec, options, evidence, OWNER)
    assert expected in {failure["code"] for failure in report["failures"]}


async def test_coupon_expiry_and_owner_reread(catalog):
    pool, ids = catalog
    cid = await coupon(pool)
    spec, options, evidence = await evidence_for(pool, ids[:1])
    report = await verify_options(pool, spec, options, evidence, OTHER)
    assert not report["passed"]
    await pool.execute("UPDATE coupons SET valid_until=NOW()-INTERVAL '1 second' WHERE id=$1", cid)
    report = await verify_options(pool, spec, options, evidence, OWNER)
    assert "COUPON_CHANGED" in {failure["code"] for failure in report["failures"]}


async def test_unknown_sku_rejected(catalog):
    pool, _ids = catalog
    with pytest.raises(DomainError) as caught:
        await execute_capability(pool, "quote_options", specification(), {"product_ids": [str(uuid4())]}, OWNER)
    assert caught.value.code == "UNKNOWN_PRODUCT"


async def test_budget_no_reviews_and_duplicate_options_are_not_success(catalog):
    pool, ids = catalog
    spec, options, evidence = await evidence_for(pool, ids[:1], specification(total_budget="199.99"))
    report = await verify_options(pool, spec, options, evidence, OWNER)
    assert "BUDGET_EXCEEDED" in {failure["code"] for failure in report["failures"]}
    await pool.execute("DELETE FROM reviews WHERE product_id=$1", ids[0])
    spec, options, evidence = await evidence_for(pool, ids[:1])
    report = await verify_options(pool, spec, options, evidence, OWNER)
    assert "INSUFFICIENT_REVIEWS" in {failure["code"] for failure in report["failures"]}


async def test_previous_requirement_evidence_cannot_cross_versions(catalog):
    pool, ids = catalog
    spec, options, evidence = await evidence_for(pool, ids[:1])
    changed = {**spec, "query": "键盘"}
    report = await verify_options(pool, changed, options, evidence, OWNER)
    assert not report["passed"]
    assert "INVALID_EVIDENCE" in {failure["code"] for failure in report["failures"]}


async def test_forged_candidate_source_cannot_include_unrelated_product(catalog):
    pool, ids = catalog
    spec, options, evidence = await evidence_for(pool, ids[:1])
    await pool.execute("UPDATE products SET name='kettle',description='kettle only' WHERE id=$1", ids[0])
    options[0]["name"] = "kettle"
    report = await verify_options(pool, spec, options, evidence, OWNER)
    assert not report["passed"]
    assert any(failure["capabilities"] == ["search_candidates"] for failure in report["failures"])


async def test_budget_change_reuses_facts_but_rechecks_feasibility(catalog):
    pool, ids = catalog
    spec, options, evidence = await evidence_for(pool, ids[:1])
    report = await verify_options(pool, {**spec, "total_budget": "210"}, options, evidence, OWNER)
    assert report["passed"]
    report = await verify_options(pool, {**spec, "total_budget": "199"}, options, evidence, OWNER)
    assert {failure["code"] for failure in report["failures"]} == {"BUDGET_EXCEEDED"}


async def test_refreshed_search_permits_same_sku_evidence_reuse(catalog):
    pool, ids = catalog
    spec, options, evidence = await evidence_for(pool, ids[:1])
    changed = {**spec, "query": "headphones", "min_rating": 4}
    evidence[0] = await execute_capability(pool, "search_candidates", changed, {}, OWNER)
    report = await verify_options(pool, changed, options, evidence, OWNER)
    assert report["passed"], report


async def test_demo_seed_rejects_wrong_database(catalog):
    from scripts.seed_procurement import seed

    pool, _ = catalog
    before = await pool.fetchval("SELECT count(*) FROM products")
    with pytest.raises(RuntimeError, match="reliable_commerce_demo"):
        await seed(pool)
    assert await pool.fetchval("SELECT count(*) FROM products") == before


async def test_demo_seed_is_append_only_on_isolated_demo_database(database_url):
    from pathlib import Path

    import asyncpg
    from scripts.seed_procurement import DEMO_EMAIL, entity_id, seed

    admin = await asyncpg.connect(database_url)
    # This server was created exclusively by the testcontainers fixture.
    await admin.execute("CREATE DATABASE reliable_commerce_demo")
    pool = await asyncpg.create_pool(database_url.rsplit("/", 1)[0] + "/reliable_commerce_demo")
    try:
        await pool.execute((Path(__file__).resolve().parents[3] / "docker/postgres/init.sql").read_text())
        await pool.execute("INSERT INTO users(email,password_hash,name) VALUES($1,'unused','合成演示账号')", DEMO_EMAIL)
        old_id = uuid4()
        await pool.execute(
            "INSERT INTO products(id,name,description,category,price) VALUES($1,'旧商品','保持原状','Books',7)",
            old_id,
        )
        first = await seed(pool)
        await pool.execute("UPDATE warehouse_inventory SET quantity=3 WHERE product_id=$1", entity_id("a"))
        await pool.execute("UPDATE coupons SET times_used=2 WHERE id=$1", entity_id("coupon"))
        second = await seed(pool)
        assert first["products"] == second["products"]
        assert await pool.fetchval("SELECT count(*) FROM products") == 6
        assert await pool.fetchval("SELECT price FROM products WHERE id=$1", old_id) == Decimal("7")
        assert await pool.fetchval("SELECT quantity FROM warehouse_inventory WHERE product_id=$1", entity_id("a")) == 3
        assert await pool.fetchval("SELECT times_used FROM coupons WHERE id=$1", entity_id("coupon")) == 2
    finally:
        await pool.close()
        await admin.execute("DROP DATABASE reliable_commerce_demo")
        await admin.close()


async def test_search_row_reference_cannot_be_forged(catalog):
    pool, ids = catalog
    spec, options, evidence = await evidence_for(pool, ids[:1])
    evidence[0]["items"][0]["source_ids"] = {"products": [str(uuid4())]}
    report = await verify_options(pool, spec, options, evidence, OWNER)
    assert not report["passed"]
    assert "INVALID_EVIDENCE" in {failure["code"] for failure in report["failures"]}


async def test_inventory_does_not_hide_negative_warehouse_adjustments(catalog):
    pool, ids = catalog
    warehouse = uuid4()
    await pool.execute("INSERT INTO warehouses(id,name,location,region) VALUES($1,'调整仓','合成','west')", warehouse)
    await pool.execute(
        "INSERT INTO warehouse_inventory(warehouse_id,product_id,quantity) VALUES($1,$2,-9)",
        warehouse,
        ids[0],
    )
    result = await execute_capability(pool, "check_inventory", specification(), {"product_ids": ids[:1]}, OWNER)
    assert result["items"][0]["available_quantity"] == 1
    assert result["items"][0]["sufficient"] is False
