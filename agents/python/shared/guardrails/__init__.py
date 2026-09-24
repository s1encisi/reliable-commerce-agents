"""多智能体平台的代码层安全护栏。

在提示词规则之外增加纵深防护：sanitize 净化不可信文本；输出中间件
在工具结果返回模型前净化；输入中间件观察或阻止注入；requires_role
在人工审批前检查调用角色。所有功能通过 GUARDRAILS_* 配置控制，
默认可先观察再逐步启用阻止策略。
"""

from __future__ import annotations

from shared.guardrails.sanitize import (
    contains_injection_markers,
    neutralize_text,
    neutralize_value,
)

__all__ = [
    "contains_injection_markers",
    "neutralize_text",
    "neutralize_value",
]
