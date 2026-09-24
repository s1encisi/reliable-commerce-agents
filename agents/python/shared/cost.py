"""根据 token 数和模型价格表估算费用。

统一供评测报告和运行时用量记录使用，替代各调用方独立硬编码价格。
价格单位是每千 token 的美元费用；提供方调价后需更新 _PRICING。
未知模型返回 None；新提供方的本币用量与费用上界由累计账本记录。
这些值是估算依据，不能代替账单。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_per_1k: float
    output_per_1k: float


# 每千 token 的美元价格。键保持小写，
# 调用方可能直接传入 LLM_MODEL 或 AZURE_OPENAI_DEPLOYMENT，
# 常见部署名也采用小写形式。
_PRICING: dict[str, ModelPricing] = {
    "gpt-4.1": ModelPricing(input_per_1k=0.002, output_per_1k=0.008),
    "gpt-4.1-mini": ModelPricing(input_per_1k=0.0004, output_per_1k=0.0016),
    "gpt-4.1-nano": ModelPricing(input_per_1k=0.0001, output_per_1k=0.0004),
    "gpt-4o": ModelPricing(input_per_1k=0.0025, output_per_1k=0.01),
    "gpt-4o-mini": ModelPricing(input_per_1k=0.00015, output_per_1k=0.0006),
    "text-embedding-3-small": ModelPricing(input_per_1k=0.00002, output_per_1k=0.0),
    "text-embedding-3-large": ModelPricing(input_per_1k=0.00013, output_per_1k=0.0),
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """估算已知模型的美元费用；未知模型返回 None，不猜测价格。"""
    pricing = _PRICING.get(model.lower())
    if pricing is None:
        # 未知提供方／本币价格不能伪装成 GPT-4.1 的美元费用。
        return None
    return (tokens_in / 1000) * pricing.input_per_1k + (tokens_out / 1000) * pricing.output_per_1k
