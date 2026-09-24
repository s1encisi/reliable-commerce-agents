"""工具结果解包与重新包装的纯逻辑测试。

覆盖真实 list[Content] 包装和裸字典、列表透传，防止中间件在
真实运行时静默忽略工具数据。
"""

from __future__ import annotations

import json

from shared.function_results import rewrap_function_result, unwrap_function_result


class _FakeContent:
    """仅模拟 Content 的 text 属性，满足辅助函数使用范围。"""

    def __init__(self, text: str | None) -> None:
        self.text = text


def test_unwrap_passes_through_a_raw_dict_unchanged() -> None:
    raw = {"price": 19.99, "name": "Widget"}
    assert unwrap_function_result(raw) == raw


def test_unwrap_passes_through_a_raw_list_unchanged() -> None:
    raw = [{"id": "1"}, {"id": "2"}]
    assert unwrap_function_result(raw) == raw


def test_unwrap_parses_json_text_from_wrapped_content() -> None:
    wrapped = [_FakeContent(json.dumps({"price": 299.99, "product_id": "p1"}))]
    assert unwrap_function_result(wrapped) == {"price": 299.99, "product_id": "p1"}


def test_unwrap_parses_a_json_array_from_wrapped_content() -> None:
    wrapped = [_FakeContent(json.dumps([{"id": "1"}, {"id": "2"}]))]
    assert unwrap_function_result(wrapped) == [{"id": "1"}, {"id": "2"}]


def test_unwrap_returns_none_for_empty_text() -> None:
    assert unwrap_function_result([_FakeContent(None)]) is None
    assert unwrap_function_result([_FakeContent("")]) is None


def test_unwrap_returns_raw_text_when_not_valid_json() -> None:
    wrapped = [_FakeContent("not json")]
    assert unwrap_function_result(wrapped) == "not json"


def test_rewrap_mutates_wrapped_content_text_in_place() -> None:
    original = [_FakeContent(json.dumps({"price": 19.99}))]
    result = rewrap_function_result(original, {"price": 25.00})
    assert result is original  # 仍为同一对象，内容原地修改。
    assert json.loads(original[0].text) == {"price": 25.00}


def test_rewrap_returns_new_value_directly_for_a_raw_dict() -> None:
    original = {"price": 19.99}
    result = rewrap_function_result(original, {"price": 25.00})
    assert result == {"price": 25.00}


def test_unwrap_then_rewrap_round_trips() -> None:
    original = [_FakeContent(json.dumps({"reviews": [{"body": "great!"}]}))]
    unwrapped = unwrap_function_result(original)
    unwrapped["reviews"][0]["body"] = "[REDACTED]"
    rewrapped = rewrap_function_result(original, unwrapped)
    assert json.loads(rewrapped[0].text) == {"reviews": [{"body": "[REDACTED]"}]}
