"""replay 模式使用的确定性离线向量嵌入。

词元经特征哈希映射到带符号的桶，求和后 L2 归一化；共享词元的文本
具有较近余弦距离。这样可实际执行 pgvector 检索，而无需模型密钥。

它验证检索链路，不具备同义词理解能力，也不代表真实语义模型质量。
商品写入和查询必须使用同一 embed_text 实现，否则相似度会失去意义。
集中实现也避免维护大量真实向量夹具及重新录制成本。
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

# 维度与 text-embedding-3-small 一致，
# 可直接写入已有 vector(1536) 列，无需修改表结构。
EMBEDDING_DIMENSIONS = 1536

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _bucket(token: str) -> tuple[int, float]:
    """把词元映射为（桶索引，带符号的权重）。

    索引和符号取自同一摘要的不同字节，让碰撞词元倾向于相互抵消，
    避免无关文本因哈希冲突获得虚假的相似度。

    使用 hashlib 而非每个进程随机加盐的 hash()，确保种子脚本与智能体
    进程计算出相同向量；第 14 章曾出现过 PYTHONHASHSEED 导致的不一致。

    固定使用 SHA-256，保证商品向量和查询向量的分桶规则一致。
    修改该函数后，必须重新录制执行轨迹中包含语义检索的回放夹具。
    """
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
    sign = 1.0 if digest[4] & 1 else -1.0
    return index, sign


def embed_text(text: str) -> list[float]:
    """为 text 生成确定性的单位向量。"""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for token in _TOKEN_RE.findall(text.lower()):
        index, sign = _bucket(token)
        vector[index] += sign

    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        # 空文本或纯标点不能产生零向量，因其余弦距离无定义，
        # 会使 pgvector 排序出现 NaN；改用固定锚点。
        vector[0] = 1.0
        return vector
    return [v / norm for v in vector]


# 保持调用方现有接口的最小响应结构。
#
# 调用方仍使用 embeddings.create(model=..., input=[...])，
# 并读取 response.data[i].embedding。
# 保持接口形态，调用点就不必判断提供方，
# 与 ReplayChatClient 的兼容设计一致。


@dataclass(frozen=True)
class _Embedding:
    embedding: list[float]
    index: int


@dataclass(frozen=True)
class _EmbeddingsResponse:
    data: list[_Embedding]
    model: str


class _Embeddings:
    async def create(self, *, model: str, input: list[str] | str, **_: object) -> _EmbeddingsResponse:
        texts = [input] if isinstance(input, str) else list(input)
        return _EmbeddingsResponse(
            data=[_Embedding(embedding=embed_text(t), index=i) for i, t in enumerate(texts)],
            model=model,
        )


class ReplayEmbeddingsClient:
    """LLM_PROVIDER=replay 时使用的离线嵌入客户端。"""

    def __init__(self) -> None:
        self.embeddings = _Embeddings()

    async def close(self) -> None:  # 与真实异步客户端接口保持一致。
        return None
