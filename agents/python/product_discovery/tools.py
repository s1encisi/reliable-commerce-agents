"""商品发现工具 —— 搜索、对比、语义搜索、热门商品。"""

from __future__ import annotations

import json
from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.agent_factory import create_embedding_client, get_embedding_model
from shared.db import get_pool
from shared.search import RRF_K, or_joined_tsquery
from shared.tool_inputs import clamp_limit

SORT_CLAUSES = {
    "price_asc": "p.price ASC",
    "price_desc": "p.price DESC",
    "rating": "p.rating DESC",
    "newest": "p.created_at DESC",
}
RATING_SORT = "p.rating DESC, p.review_count DESC"


@tool(
    name="search_products",
    description=(
        "Search the product catalog using natural language. Supports filtering by category, price range, and rating."
    ),
)
async def search_products(
    query: Annotated[
        str | None, Field(description="Natural language search query (optional if using category filter)")
    ] = None,
    category: Annotated[
        str | None, Field(description="Filter by category: Electronics, Clothing, Home, Sports, Books")
    ] = None,
    min_price: Annotated[float | None, Field(description="Minimum price filter")] = None,
    max_price: Annotated[float | None, Field(description="Maximum price filter")] = None,
    min_rating: Annotated[float | None, Field(description="Minimum rating (1-5)")] = None,
    sort_by: Annotated[str | None, Field(description="Sort by: price_asc, price_desc, rating, newest")] = None,
    limit: Annotated[int, Field(description="Max results to return")] = 10,
) -> list[dict]:
    pool = get_pool()
    safe_limit = clamp_limit(limit, default=10, maximum=100)
    conditions = ["p.is_active = TRUE"]
    args: list = []
    idx = 1

    # 类别已由专门过滤器限定，不要把相同类别名重复当作全文关键词。
    if category and query and query.strip().casefold() == category.casefold():
        query = None
    if query:
        from shared.search import expand_catalog_query

        query = expand_catalog_query(query)

    # 基于加权 search_vector 列做 Postgres 全文检索。
    tsquery: str | None = None
    if query and query.strip():
        tsquery = or_joined_tsquery(f"${idx}")
        # 仅含停用词或标点的查询（"the"、"???"）会归约为空 tsquery，
        # 匹配不到任何行。此时按"无文本查询"处理，让其余过滤条件独立生效。
        conditions.append(f"({tsquery} = ''::tsquery OR p.search_vector @@ {tsquery})")
        args.append(query)
        idx += 1

    if category:
        conditions.append(f"p.category = ${idx}")
        args.append(category)
        idx += 1

    if min_price is not None:
        conditions.append(f"p.price >= ${idx}")
        args.append(min_price)
        idx += 1

    if max_price is not None:
        conditions.append(f"p.price <= ${idx}")
        args.append(max_price)
        idx += 1

    if min_rating is not None:
        conditions.append(f"p.rating >= ${idx}")
        args.append(min_rating)
        idx += 1

    # 显式指定 sort_by 时始终优先。否则在有查询时按文本相关性排序
    # （旧代码无论有没有查询都按评分排序，导致一个弱匹配但评价多的商品
    # 排在精确匹配之前），没有查询时按评分排序。
    if sort_by in SORT_CLAUSES:
        order = SORT_CLAUSES[sort_by]
    elif tsquery:
        order = f"ts_rank(p.search_vector, {tsquery}) DESC, {RATING_SORT}"
    else:
        order = RATING_SORT

    where = " AND ".join(conditions)
    sql = f"""
        SELECT p.id, p.name, p.description, p.category, p.brand, p.price,
               p.original_price, p.rating, p.review_count, p.specs, p.image_url
        FROM products p
        WHERE {where}
        ORDER BY {order}
        LIMIT {safe_limit}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "description": r["description"][:150],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "original_price": float(r["original_price"]) if r["original_price"] else None,
                "on_sale": r["original_price"] is not None and r["price"] < r["original_price"],
                "rating": float(r["rating"]),
                "review_count": r["review_count"],
                "image_url": r["image_url"],
            }
            for r in rows
        ]


@tool(name="get_product_details", description="Get complete details for a specific product including full specs.")
async def get_product_details(
    product_id: Annotated[str, Field(description="UUID of the product")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, name, description, category, brand, price, original_price,
                      image_url, rating, review_count, specs
               FROM products WHERE id = $1""",
            product_id,
        )
        if not row:
            return {"error": f"Product not found: {product_id}"}

        return {
            "id": str(row["id"]),
            "name": row["name"],
            "description": row["description"],
            "category": row["category"],
            "brand": row["brand"],
            "price": float(row["price"]),
            "original_price": float(row["original_price"]) if row["original_price"] else None,
            "on_sale": row["original_price"] is not None and row["price"] < row["original_price"],
            "rating": float(row["rating"]),
            "review_count": row["review_count"],
            "specs": json.loads(row["specs"]) if isinstance(row["specs"], str) else dict(row["specs"]),
        }


@tool(name="compare_products", description="Compare 2-3 products side-by-side on key attributes.")
async def compare_products(
    product_ids: Annotated[list[str], Field(description="List of 2-3 product UUIDs to compare")],
) -> list[dict]:
    if len(product_ids) < 2 or len(product_ids) > 3:
        return [{"error": "Please provide 2-3 product IDs to compare"}]

    pool = get_pool()
    results = []
    async with pool.acquire() as conn:
        for pid in product_ids:
            row = await conn.fetchrow(
                """SELECT id, name, category, brand, price, original_price, rating, review_count, specs
                   FROM products WHERE id = $1""",
                pid,
            )
            if row:
                results.append(
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "category": row["category"],
                        "brand": row["brand"],
                        "price": float(row["price"]),
                        "original_price": float(row["original_price"]) if row["original_price"] else None,
                        "rating": float(row["rating"]),
                        "review_count": row["review_count"],
                        "specs": json.loads(row["specs"]) if isinstance(row["specs"], str) else dict(row["specs"]),
                    }
                )
    return results


@tool(
    name="semantic_search",
    description=(
        "Search products using semantic similarity via pgvector embeddings. Best "
        "for vague or descriptive queries like 'something cozy for winter' or 'gi"
        "ft for a tech enthusiast'."
    ),
)
async def semantic_search(
    query: Annotated[str, Field(description="Descriptive search query in natural language")],
    limit: Annotated[int, Field(description="Max results")] = 5,
) -> list[dict]:
    pool = get_pool()

    # 通过 OpenAI / Azure OpenAI 生成嵌入向量
    from shared.factory import EmbeddingsUnavailableError

    try:
        client = create_embedding_client()
    except EmbeddingsUnavailableError:
        # 不调用错误的提供方，不把词法结果标记成语义相似度。
        rows = await search_products.func(query=query, limit=limit)
        return [{**row, "retrieval_mode": "lexical", "embedding_available": False} for row in rows]
    response = await client.embeddings.create(model=get_embedding_model(), input=[query])
    embedding = response.data[0].embedding

    # 从每一路召回比最终返回更多的候选 —— 只有当某个文档出现在一路列表中
    # 而没出现在另一路时，融合才有可用的信息。
    candidates = max(limit * 4, 20)

    # 混合检索：分别按向量余弦相似度和全文相关性排序，再用
    # Reciprocal Rank Fusion 融合。RRF 对各路累加 1/(k+rank)，
    # 因此两路都认可的商品会排在只在一路登顶的商品之前，
    # 并且两路的原始分数无需处于可比的量纲上。
    sql = f"""
        WITH vec AS (
            SELECT pe.product_id,
                   1 - (pe.embedding <=> $1::vector) AS similarity,
                   ROW_NUMBER() OVER (ORDER BY pe.embedding <=> $1::vector) AS rank
            FROM product_embeddings pe
            JOIN products p ON pe.product_id = p.id
            WHERE p.is_active = TRUE
            LIMIT $3
        ),
        fts AS (
            SELECT p.id AS product_id,
                   ROW_NUMBER() OVER (ORDER BY ts_rank(p.search_vector, {or_joined_tsquery("$2")}) DESC) AS rank
            FROM products p
            WHERE p.is_active = TRUE
              AND {or_joined_tsquery("$2")} <> ''::tsquery
              AND p.search_vector @@ {or_joined_tsquery("$2")}
            LIMIT $3
        ),
        fused AS (
            SELECT COALESCE(v.product_id, f.product_id) AS product_id,
                   v.similarity,
                   COALESCE(1.0 / ({RRF_K} + v.rank), 0) + COALESCE(1.0 / ({RRF_K} + f.rank), 0) AS score
            FROM vec v
            FULL OUTER JOIN fts f ON v.product_id = f.product_id
        )
        SELECT p.id, p.name, p.description, p.category, p.brand, p.price, p.rating, p.image_url,
               fu.similarity, fu.score
        FROM fused fu
        JOIN products p ON p.id = fu.product_id
        ORDER BY fu.score DESC, p.rating DESC
        LIMIT $4
    """

    async with pool.acquire() as conn:
        # 为该查询调高 ivfflat 的探测数（#52）。
        #
        # `idx_product_embedding` 由 init.sql 在空表上创建，因此 ivfflat
        # 没有数据可用来推导质心，每个向量都会落进一个退化的分区。在默认
        # `probes = 1` 下，查询只探测一个列表，返回其中的内容 —— 或者什么都
        # 不返回。在一个已灌入数据的数据库上实测："wireless noise cancelling
        # headphones" 经索引返回的是相似度 0.000 的 "Patagonia Better
        # Sweater"，而精确扫描返回的是相似度 0.420 的 "Sony WH-1000XM5"。
        # 同样的数据，同样的查询。
        #
        # generate_embeddings.py 现在会在写入后 REINDEX，修复了正常路径，
        # 但任何其他插入（一次测试、一个新商品）都会让索引重新变陈旧。
        # 探测所有列表可以让正确性不依赖于是否有人记得重建索引。在当前
        # 目录规模下这没有额外代价 —— `lists = 10` 时这相当于对 50 行做精确
        # 搜索 —— 而如果目录增长到索引真正物有所值，它也只是退化为普通的
        # 召回率/延迟权衡。
        #
        # 在 RRF 下这一点比过去更重要：向量这一路现在贡献的是一个 *排名*，
        # 因此退化的探测不只是返回一行弱结果，它会把错误的排序喂给融合。
        #
        # SET LOCAL 只在事务内生效；在事务外它是一个只会告警而不报错的
        # 空操作，这本身就是个安静的陷阱。
        async with conn.transaction():
            await conn.execute("SET LOCAL ivfflat.probes = 10")
            rows = await conn.fetch(sql, json.dumps(embedding), query, candidates, limit)

        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "description": r["description"][:150],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "rating": float(r["rating"]),
                # 仅当文本这一路匹配时，该值为 None —— 说明该商品没有嵌入
                # 记录，或排名落在向量候选窗口之外。
                "similarity": round(float(r["similarity"]), 3) if r["similarity"] is not None else None,
                "score": round(float(r["score"]), 5),
                "image_url": r["image_url"],
            }
            for r in rows
        ]


@tool(
    name="find_similar_products", description="Find products similar to a given product based on embedding similarity."
)
async def find_similar_products(
    product_id: Annotated[str, Field(description="UUID of the reference product")],
    limit: Annotated[int, Field(description="Max results")] = 5,
) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        # 获取参考商品的嵌入向量
        ref = await conn.fetchrow(
            "SELECT embedding FROM product_embeddings WHERE product_id = $1",
            product_id,
        )
        if not ref:
            return [{"error": f"No embedding found for product {product_id}"}]

        # 与 semantic_search 一样存在 ivfflat 陈旧索引问题 —— 参见那里的长注释。
        # 该查询命中同一个索引，因此需要同样的探测数，否则它同样会返回
        # 无关商品（或什么都不返回）。
        async with conn.transaction():
            await conn.execute("SET LOCAL ivfflat.probes = 10")
            rows = await conn.fetch(
                """SELECT p.id, p.name, p.category, p.brand, p.price, p.rating,
                          1 - (pe.embedding <=> $1) as similarity
                   FROM product_embeddings pe
                   JOIN products p ON pe.product_id = p.id
                   WHERE pe.product_id != $2 AND p.is_active = TRUE
                   ORDER BY pe.embedding <=> $1
                   LIMIT $3""",
                ref["embedding"],
                product_id,
                limit,
            )
        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "rating": float(r["rating"]),
                "similarity": round(float(r["similarity"]), 3),
            }
            for r in rows
        ]


@tool(name="get_trending_products", description="Get trending products based on recent order volume.")
async def get_trending_products(
    category: Annotated[str | None, Field(description="Optional category filter")] = None,
    days: Annotated[int, Field(description="Trending period in days")] = 30,
    limit: Annotated[int, Field(description="Max results")] = 10,
) -> list[dict]:
    pool = get_pool()
    safe_limit = clamp_limit(limit, default=10, maximum=50)
    conditions = ["o.created_at >= NOW() - ($1 || ' days')::interval"]
    args: list = [str(days)]
    idx = 2

    if category:
        conditions.append(f"p.category = ${idx}")
        args.append(category)
        idx += 1

    where = " AND ".join(conditions)
    sql = f"""
        SELECT p.id, p.name, p.category, p.brand, p.price, p.rating,
               COUNT(oi.id) as order_count,
               SUM(oi.quantity) as units_sold
        FROM products p
        JOIN order_items oi ON oi.product_id = p.id
        JOIN orders o ON oi.order_id = o.id
        WHERE {where}
        GROUP BY p.id, p.name, p.category, p.brand, p.price, p.rating
        ORDER BY units_sold DESC
        LIMIT {safe_limit}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "rating": float(r["rating"]),
                "order_count": r["order_count"],
                "units_sold": r["units_sold"],
            }
            for r in rows
        ]
