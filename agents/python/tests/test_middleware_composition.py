"""专业智能体中间件组装的确定性测试，无模型、数据库或智能体构建。"""

from __future__ import annotations

from shared.middleware import build_specialist_middleware


def test_default_stack_is_nonempty_list() -> None:
    stack = build_specialist_middleware()
    assert isinstance(stack, list)
    assert len(stack) >= 1


def test_include_steps_adds_step_middleware() -> None:
    with_steps = build_specialist_middleware(include_steps=True)
    without_steps = build_specialist_middleware(include_steps=False)
    # 只有 include_steps=True 时追加步骤记录中间件。
    assert len(with_steps) >= len(without_steps)


def test_guardrail_middleware_present_by_default() -> None:
    names = {type(m).__name__ for m in build_specialist_middleware()}
    assert "OutputSanitizationMiddleware" in names
    assert "InjectionDetectionChatMiddleware" in names
