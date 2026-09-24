"""回放模式的语义检索回归测试。

覆盖嵌入客户端不能误入真实 OpenAI 路径，以及空表建立向量索引后
检索可能返回不相关记录的问题。端到端断言相关性，不能只断言无异常。
"""

from __future__ import annotations

import pytest

from shared.replay_embeddings import EMBEDDING_DIMENSIONS, ReplayEmbeddingsClient, embed_text


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def test_embeddings_are_deterministic_across_processes() -> None:
    """相同文本跨进程得到相同向量，不依赖 Python 随机加盐的 hash。"""
    assert embed_text("noise cancelling headphones") == embed_text("noise cancelling headphones")
    assert len(embed_text("anything")) == EMBEDDING_DIMENSIONS


def test_related_text_scores_higher_than_unrelated() -> None:
    """验证有用的相似性，而不仅是确定性。"""
    product = embed_text("Sony WH-1000XM5 | Premium wireless noise-cancelling headphones")
    related = embed_text("wireless noise cancelling headphones")
    unrelated = embed_text("stainless steel kitchen blender")

    assert _cosine(product, related) > 0.3
    assert _cosine(product, related) > _cosine(product, unrelated)


def test_vectors_are_unit_length_and_survive_empty_input() -> None:
    """零向量的余弦距离无定义，不能让 pgvector 按 NaN 排序。"""
    for text in ("headphones", "", "!!!  ---  ???"):
        norm = sum(v * v for v in embed_text(text)) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_client_matches_the_shape_call_sites_use() -> None:
    """接口与真实客户端一致：create 返回 data[i].embedding。"""
    client = ReplayEmbeddingsClient()
    response = await client.embeddings.create(model="text-embedding-3-small", input=["a", "b"])

    assert [d.index for d in response.data] == [0, 1]
    assert response.data[0].embedding == embed_text("a")
    assert response.data[1].embedding != response.data[0].embedding


def test_factory_selects_the_replay_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放工厂不能落入 OpenAI 分支。

    替换 factory 实际持有的 settings，避免重载配置后替换了另一对象，
    导致单独通过但整套测试失败。
    """
    from shared import factory

    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "replay", raising=False)
    monkeypatch.setattr(factory.settings, "OPENAI_API_KEY", "", raising=False)

    # 旧实现会因缺少 OPENAI_API_KEY 抛错。
    assert isinstance(factory.get_embeddings_client(), ReplayEmbeddingsClient)


@pytest.mark.asyncio
async def test_semantic_search_returns_relevant_products(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 pgvector 端到端验证：输入查询，返回相关商品。

    仅返回任意记录不足以发现失效索引。
    """
    import json
    import uuid

    from shared import factory

    # 按工厂实际持有的配置对象替换，
    # 原因见前面的回放工厂测试。
    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "replay", raising=False)
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    products = [
        ("Sony WH-1000XM5", "Premium wireless noise-cancelling headphones with 30-hour battery"),
        ("Hoka Clifton 9", "Lightweight cushioned running shoes for long distance road runs"),
        ("Breville Barista Express", "Espresso machine with an integrated conical burr grinder"),
    ]
    for name, description in products:
        product_id = uuid.uuid4()
        await clean_db.execute(
            """INSERT INTO products (id, name, description, category, brand, price, is_active)
               VALUES ($1, $2, $3, 'test', 'test', 100.00, TRUE)""",
            product_id,
            name,
            description,
        )
        await clean_db.execute(
            "INSERT INTO product_embeddings (product_id, embedding) VALUES ($1, $2)",
            product_id,
            json.dumps(embed_text(f"{name} | {description}")),
        )

    from product_discovery.tools import semantic_search

    search = getattr(semantic_search, "func", semantic_search)

    for query, expected in (
        ("wireless noise cancelling headphones", "Sony WH-1000XM5"),
        ("shoes for running a marathon", "Hoka Clifton 9"),
        ("espresso coffee machine", "Breville Barista Express"),
    ):
        results = await search(query, 3)
        assert results, f"{query!r} returned nothing"
        assert results[0]["name"] == expected, (
            f"{query!r} ranked {results[0]['name']!r} first, expected {expected!r} — "
            f"got {[(r['name'], round(r['similarity'], 3)) for r in results]}"
        )
        assert results[0]["similarity"] > 0.1
