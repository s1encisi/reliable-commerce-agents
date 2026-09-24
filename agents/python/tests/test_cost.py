"""费用估算纯逻辑测试，不使用数据库或模型。"""

from __future__ import annotations

from shared.cost import estimate_cost


def test_known_model_computes_correct_price() -> None:
    # 测试采用既有价格表：每千输入 0.002 美元、输出 0.008 美元。
    cost = estimate_cost("gpt-4.1", tokens_in=1000, tokens_out=1000)
    assert cost == 0.002 + 0.008


def test_zero_tokens_is_zero_cost() -> None:
    assert estimate_cost("gpt-4.1", 0, 0) == 0.0


def test_unknown_model_is_not_mispriced_as_default() -> None:
    cost = estimate_cost("some-custom-deployment-name", tokens_in=1000, tokens_out=1000)
    assert cost is None


def test_model_name_matching_is_case_insensitive() -> None:
    assert estimate_cost("GPT-4.1", 1000, 0) == estimate_cost("gpt-4.1", 1000, 0)


def test_embedding_model_has_no_output_cost() -> None:
    cost = estimate_cost("text-embedding-3-small", tokens_in=1000, tokens_out=1000)
    # 嵌入模型的计价不应受输出 token 数影响。
    assert cost == estimate_cost("text-embedding-3-small", tokens_in=1000, tokens_out=0)
