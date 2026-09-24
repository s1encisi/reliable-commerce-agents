"""商品全文检索与 RRF 混合检索测试，使用真实 PostgreSQL。

覆盖英文词干变化、部分词匹配以及按相关性而非仅评分排序。
向量直接写入测试表，嵌入客户端返回固定值，不调用真实模型。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

import shared.db as shared_db
from product_discovery import tools as pd_tools

pytestmark = pytest.mark.asyncio


# 测试商品让文本证据与向量证据产生可控差异，
# 并让查询词形与商品正文不同。
# 商品中不直接出现 cancellation，
# 也不出现 bluetooth，以验证词干与部分召回。
ANC = uuid.UUID("11111111-1111-4111-8111-111111111111")
EXACT = uuid.UUID("22222222-2222-4222-8222-222222222222")
DECOY = uuid.UUID("33333333-3333-4333-8333-333333333333")
KETTLE = uuid.UUID("44444444-4444-4444-8444-444444444444")

CATALOG = [
    # 字段：标识、名称、描述、分类、品牌、价格、评分、评论数。
    (
        ANC,
        "Wireless Headphones with ANC",
        "Over-ear wireless headphones with active noise cancelling for travel.",
        "Electronics",
        "Sony",
        279.99,
        4.5,
        900,
    ),
    (
        EXACT,
        "Noise Cancelling Headphones Pro",
        "Studio-grade over-ear headphones.",
        "Electronics",
        "Bose",
        349.99,
        4.1,
        50,
    ),
    (
        # 同类商品只偶然提到耳机，但评分更高，
        # 用于验证旧的纯评分排序问题。
        DECOY,
        "Phone Case",
        "Slim protective case. Works fine with headphones plugged in.",
        "Electronics",
        "Generic",
        12.99,
        5.0,
        5000,
    ),
    (
        KETTLE,
        "Electric Kettle",
        "1.7L stainless steel kettle with rapid boil.",
        "Home",
        "Breville",
        59.99,
        4.8,
        300,
    ),
]


async def _seed_catalog(pool: Any) -> None:
    async with pool.acquire() as conn:
        for pid, name, desc, category, brand, price, rating, reviews in CATALOG:
            await conn.execute(
                """INSERT INTO products
                       (id, name, description, category, brand, price, rating, review_count, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE)""",
                pid,
                name,
                desc,
                category,
                brand,
                price,
                rating,
                reviews,
            )


async def _seed_embedding(pool: Any, product_id: uuid.UUID, vector: list[float]) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO product_embeddings (product_id, embedding) VALUES ($1, $2::vector)",
            product_id,
            str(vector),
        )


def _unit_vector(index: int) -> list[float]:
    """构造 1536 维独热向量，不同维度彼此正交，便于精确控制向量排序。"""
    vec = [0.0] * 1536
    vec[index] = 1.0
    return vec


@pytest.fixture
def _pool(monkeypatch: pytest.MonkeyPatch, clean_db: Any) -> Any:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


# ─────────────────────── search_products (FTS) ───────────────────────


async def test_stemmed_term_matches(_pool: Any) -> None:
    """商品写 cancelling、用户搜 cancellation，英文词干化应统一到 cancel。"""
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="noise cancellation headphones")

    ids = [r["id"] for r in results]
    assert str(ANC) in ids
    assert str(EXACT) in ids


async def test_absent_term_does_not_zero_the_result_set(_pool: Any) -> None:
    """未出现的 bluetooth 不能让整个查询无结果，OR 应保留其他词的匹配。"""
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="wireless bluetooth headphones")

    assert str(ANC) in [r["id"] for r in results]


async def test_relevance_beats_rating(_pool: Any) -> None:
    """仅偶然提到耳机的高评分手机壳，不能压过真正相关商品。"""
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="noise cancellation headphones")

    ids = [r["id"] for r in results]
    assert str(DECOY) in ids, "the decoy should still match on 'headphones'"
    assert ids.index(str(DECOY)) > ids.index(str(ANC))
    assert ids.index(str(DECOY)) > ids.index(str(EXACT))


async def test_name_weight_outranks_description_weight(_pool: Any) -> None:
    """名称权重 A 高于描述权重 C，名称匹配应获得更高排名。"""
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="noise cancellation headphones")

    ids = [r["id"] for r in results]
    assert ids.index(str(EXACT)) < ids.index(str(ANC))


async def test_filters_compose_with_query(_pool: Any) -> None:
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="headphones", category="Electronics", max_price=300)

    ids = [r["id"] for r in results]
    assert str(ANC) in ids
    assert str(EXACT) not in ids  # 349.99 超过价格上限。
    assert str(KETTLE) not in ids  # 分类不匹配。


async def test_explicit_sort_overrides_relevance(_pool: Any) -> None:
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="headphones", sort_by="price_asc")

    prices = [r["price"] for r in results]
    assert prices == sorted(prices)


async def test_stopword_only_query_falls_back_to_filters(_pool: Any) -> None:
    """停用词和标点产生空 tsquery 时，不应让带筛选的浏览结果全部消失。"""
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="the ???", category="Home")

    assert [r["id"] for r in results] == [str(KETTLE)]


async def test_no_query_returns_filtered_catalog_by_rating(_pool: Any) -> None:
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(category="Electronics")

    ratings = [r["rating"] for r in results]
    assert len(results) == 3
    assert ratings == sorted(ratings, reverse=True)


async def test_inactive_products_are_excluded(_pool: Any) -> None:
    await _seed_catalog(_pool)
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE products SET is_active = FALSE WHERE id = $1", ANC)

    results = await pd_tools.search_products(query="noise cancellation headphones")

    assert str(ANC) not in [r["id"] for r in results]


# ─────────────────────── semantic_search (hybrid RRF) ───────────────────────


@pytest.fixture
def _stub_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """返回固定查询向量，使向量分支优先水壶、文本分支优先耳机，
    从而能有效验证融合行为。
    """

    class _FakeEmbeddings:
        async def create(self, **_kwargs: Any) -> Any:
            class _Item:
                embedding = _unit_vector(0)

            class _Response:
                data = [_Item()]

            return _Response()

    class _FakeClient:
        embeddings = _FakeEmbeddings()

    monkeypatch.setattr(pd_tools, "create_embedding_client", lambda: _FakeClient())
    monkeypatch.setattr(pd_tools, "get_embedding_model", lambda: "test-embedding-model")


async def test_hybrid_returns_text_only_match(_pool: Any, _stub_embeddings: None) -> None:
    """没有嵌入的商品仍应通过文本分支召回。"""
    await _seed_catalog(_pool)
    await _seed_embedding(_pool, KETTLE, _unit_vector(0))

    results = await pd_tools.semantic_search(query="noise cancellation headphones", limit=10)

    by_id = {r["id"]: r for r in results}
    assert str(ANC) in by_id
    assert by_id[str(ANC)]["similarity"] is None  # 仅来自文本分支。
    assert by_id[str(ANC)]["score"] > 0


async def test_hybrid_returns_vector_only_match(_pool: Any, _stub_embeddings: None) -> None:
    """无词元重合但向量最近的水壶，仍应进入混合结果。"""
    await _seed_catalog(_pool)
    await _seed_embedding(_pool, KETTLE, _unit_vector(0))

    results = await pd_tools.semantic_search(query="noise cancellation headphones", limit=10)

    by_id = {r["id"]: r for r in results}
    assert str(KETTLE) in by_id
    assert by_id[str(KETTLE)]["similarity"] is not None


async def test_both_arms_outrank_single_arm(_pool: Any, _stub_embeddings: None) -> None:
    """两路共同命中的商品应优于只在单路居首的商品。"""
    await _seed_catalog(_pool)
    # ANC 同时是近向量和强文本匹配；水壶仅向量命中。
    await _seed_embedding(_pool, ANC, _unit_vector(0))
    await _seed_embedding(_pool, KETTLE, _unit_vector(1))

    results = await pd_tools.semantic_search(query="noise cancellation headphones", limit=10)

    ids = [r["id"] for r in results]
    assert ids[0] == str(ANC)
    assert results[0]["score"] > results[1]["score"]


async def test_results_are_sorted_by_score(_pool: Any, _stub_embeddings: None) -> None:
    await _seed_catalog(_pool)
    await _seed_embedding(_pool, ANC, _unit_vector(0))
    await _seed_embedding(_pool, EXACT, _unit_vector(1))

    results = await pd_tools.semantic_search(query="noise cancellation headphones", limit=10)

    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


async def test_hybrid_respects_limit(_pool: Any, _stub_embeddings: None) -> None:
    await _seed_catalog(_pool)
    await _seed_embedding(_pool, ANC, _unit_vector(0))
    await _seed_embedding(_pool, KETTLE, _unit_vector(1))

    results = await pd_tools.semantic_search(query="headphones", limit=2)

    assert len(results) == 2
