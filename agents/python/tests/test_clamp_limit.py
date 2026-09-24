"""模型控制 LIMIT 的边界回归测试。

所有 SQL 插值前必须经过 clamp_limit，避免重构扩大输入攻击面。
"""

from __future__ import annotations

import pytest

from shared.tool_inputs import clamp_limit


def test_clamp_returns_int_for_normal_values() -> None:
    assert clamp_limit(10) == 10
    assert clamp_limit(1) == 1


def test_clamp_uses_default_for_none() -> None:
    assert clamp_limit(None) == 10
    assert clamp_limit(None, default=25) == 25


def test_clamp_uses_default_for_zero_and_negative() -> None:
    assert clamp_limit(0) == 10
    assert clamp_limit(-1) == 10
    assert clamp_limit(-99999, default=50) == 50


def test_clamp_caps_at_maximum() -> None:
    assert clamp_limit(10_000) == 100
    assert clamp_limit(500, maximum=200) == 200


def test_clamp_rejects_non_numeric() -> None:
    assert clamp_limit("100; DROP TABLE products") == 10
    assert clamp_limit({"evil": True}) == 10
    assert clamp_limit([1, 2]) == 10


def test_clamp_coerces_numeric_string() -> None:
    # 模型可能把整数 5 作为字符串 "5" 返回。
    assert clamp_limit("5") == 5
    assert clamp_limit("5000") == 100  # 仍受上限约束。


def test_clamp_respects_explicit_maximum_over_value() -> None:
    # 输入与 maximum 都很大时，最终硬上限仍生效。
    assert clamp_limit(10_000_000, maximum=50) == 50


def test_clamp_float_like_strings_fall_back_to_default() -> None:
    # 10.5 不是整数文本，应退回默认值。
    assert clamp_limit("10.5") == 10


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 10),
        (1, 1),
        (10, 10),
        (99, 99),
        (100, 100),
        (101, 100),
        (1_000_000, 100),
    ],
)
def test_clamp_boundaries(value, expected) -> None:
    assert clamp_limit(value) == expected


# ─────────────── Callsite wiring sanity ───────────────


def test_every_tool_imports_clamp_limit() -> None:
    """检查工具仍导入 clamp_limit，捕获绕过该入口的结构漂移。"""
    import pathlib

    expected = {
        "order_management/tools.py",
        "shared/tools/memory_tools.py",
        "shared/tools/seller_tools.py",
        "product_discovery/tools.py",
        "orchestrator/routes/legacy.py",
    }
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in expected:
        text = (root / rel).read_text(encoding="utf-8")
        assert "clamp_limit" in text, f"clamp_limit not imported by {rel}"
