"""
可靠电商多智能体平台 —— 商品向量生成器

从数据库读取全部商品，使用 OpenAI / Azure OpenAI 的 text-embedding-3-small
（1536 维）生成向量，并把结果写入 product_embeddings 表。

用法: uv run python -m scripts.generate_embeddings

当 LLM_PROVIDER=replay 时会跳过任何真实的向量 API 调用，改为生成确定性的伪随机
向量（以商品文本为随机种子，因此重复运行结果可复现）—— 免费且确定性的持续集成
冒烟作业正是这样做的：它需要 product_embeddings 里有数据，否则依赖语义检索的
评测用例会在空表上报错；但和该作业的其他环节一样，它必须保持零成本、零凭据。
此模式下向量取值本身没有意义，冒烟套件里没有任何断言检查向量相似度质量，只
验证整条流水线能跑通。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random

import asyncpg
import openai

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents"
)
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSIONS = 1536
BATCH_SIZE = 20  # 每批输入数量上限沿用现有配置。


def _fake_embedding(text: str) -> list[float]:
    """回放模式的确定性离线商品向量。

    与查询端共用 shared.replay_embeddings.embed_text，保证相似度可比。
    向量来自商品文本而非商品标识的随机噪声，因此共享词元的查询可
    召回相关商品，真实覆盖 pgvector 路径。
    """
    from shared.replay_embeddings import embed_text

    return embed_text(text)


def create_client() -> openai.AsyncOpenAI:
    """按 LLM_PROVIDER 创建嵌入客户端。"""
    if LLM_PROVIDER == "azure":
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        key = os.environ.get("AZURE_OPENAI_KEY", "")
        if not endpoint or not key:
            raise ValueError(
                "Azure OpenAI requires AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY. "
                "Set them in .env or switch LLM_PROVIDER=openai."
            )
        return openai.AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=key,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
        )
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError(
            "OpenAI requires OPENAI_API_KEY. Set it in .env or switch LLM_PROVIDER=azure."
        )
    return openai.AsyncOpenAI(api_key=api_key)


def build_embedding_text(product: dict) -> str:
    """组合商品的丰富文本表示，供向量嵌入使用。"""
    parts = [
        product["name"],
        product["description"],
        f"Category: {product['category']}",
        f"Brand: {product['brand']}" if product["brand"] else "",
        f"Price: ${product['price']:.2f}",
    ]
    if product["specs"]:
        specs = json.loads(product["specs"]) if isinstance(product["specs"], str) else product["specs"]
        for k, v in specs.items():
            parts.append(f"{k}: {v}")
    return " | ".join(p for p in parts if p)


async def main() -> None:
    logger.info("正在连接数据库...")
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        products = await conn.fetch(
            "SELECT id, name, description, category, brand, price, specs FROM products ORDER BY name"
        )
        logger.info("共找到 %d 个商品", len(products))

        if not products:
            logger.warning("未找到任何商品 —— 请先运行 seed.py")
            return

        # 清空已有的向量
        await conn.execute("DELETE FROM product_embeddings")
        logger.info("已清空既有向量")

        if LLM_PROVIDER == "replay":
            logger.info("使用 LLM_PROVIDER=replay —— 生成确定性的模拟向量，不调用 API")
            for product in products:
                # 使用与真实提供商完全相同的文本，这样 replay 与真实运行
                # 索引的是同一份内容。
                embedding = _fake_embedding(build_embedding_text(dict(product)))
                await conn.execute(
                    "INSERT INTO product_embeddings (product_id, embedding) VALUES ($1, $2)",
                    product["id"], json.dumps(embedding),
                )
        else:
            client = create_client()
            azure_deployment = os.environ.get("AZURE_EMBEDDING_DEPLOYMENT", "")
            model = azure_deployment if LLM_PROVIDER == "azure" and azure_deployment else EMBEDDING_MODEL
            logger.info("使用 LLM_PROVIDER=%s，向量模型=%s", LLM_PROVIDER, model)

            # 分批处理
            for i in range(0, len(products), BATCH_SIZE):
                batch = products[i:i + BATCH_SIZE]
                texts = [build_embedding_text(dict(p)) for p in batch]

                logger.info("正在为第 %d/%d 批（%d 个商品）生成向量...",
                            i // BATCH_SIZE + 1, (len(products) + BATCH_SIZE - 1) // BATCH_SIZE, len(batch))

                response = await client.embeddings.create(model=model, input=texts)

                for j, embedding_data in enumerate(response.data):
                    product_id = batch[j]["id"]
                    embedding = embedding_data.embedding
                    await conn.execute(
                        "INSERT INTO product_embeddings (product_id, embedding) VALUES ($1, $2)",
                        product_id, json.dumps(embedding),
                    )

        # 重建 ivfflat 索引。这不是日常维护 —— 少了它，无论生产环境还是 replay
        # 模式，语义检索都会返回近乎垃圾的结果。
        #
        # docker/postgres/init.sql 是在**空表**上创建 `idx_product_embedding` 的，
        # 因此 ivfflat 没有任何数据可以推导质心。于是每个向量都会落进退化分区，
        # 在默认的 `ivfflat.probes = 1` 下，一次查询只探测一个列表并返回其中的
        # 全部内容。在本 schema 上直接实测：走索引时 "wireless noise cancelling
        # headphones" 返回了 "Patagonia Better Sweater"，相似度 0.000；而精确扫描
        # 返回的是 "Sony WH-1000XM5"，相似度 0.420。同样的数据、同样的查询 ——
        # 唯一的差别就是索引。
        #
        # 任何整体重新生成向量之后同理：为旧向量算出的质心并不能描述新向量。
        await conn.execute("REINDEX INDEX idx_product_embedding")
        logger.info("已重建 idx_product_embedding，使 ivfflat 质心与已存向量匹配")

        total = await conn.fetchval("SELECT COUNT(*) FROM product_embeddings")
        logger.info("已生成并存储 %d 条商品向量（维度: 1536）", total)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
