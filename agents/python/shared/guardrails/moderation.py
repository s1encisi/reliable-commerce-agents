"""对模型输出执行粗粒度的本地内容审核。

与净化输入中的恶意指令不同，这里检查模型自己生成的自伤、暴力、
仇恨、骚扰和色情等内容。即使抵御了注入，自由生成文本仍可能有风险。

全部是纯函数，不执行 I/O、模型或外部 API 调用。少量规则优先降低
误报，不追求完整召回；这不是训练分类器，间接措辞可能漏检。
详见 docs/concepts/10-guardrails.md 的分层防护说明。
"""

from __future__ import annotations

import re
from enum import StrEnum


class ModerationCategory(StrEnum):
    SELF_HARM = "self_harm"
    VIOLENCE = "violence"
    HATE_HARASSMENT = "hate_harassment"
    SEXUAL = "sexual"


_PATTERNS: dict[ModerationCategory, tuple[re.Pattern[str], ...]] = {
    ModerationCategory.SELF_HARM: (
        re.compile(r"\bkill\s+(?:myself|yourself)\b", re.I),
        re.compile(r"\b(?:commit|committing)\s+suicide\b", re.I),
        re.compile(r"\bways?\s+to\s+(?:end|take)\s+(?:my|your|his|her|their)\s+(?:own\s+)?life\b", re.I),
        re.compile(r"\bself[\s-]harm\b", re.I),
    ),
    ModerationCategory.VIOLENCE: (
        re.compile(r"\bhow\s+to\s+(?:build|make)\s+a\s+(?:bomb|explosive|weapon)\b", re.I),
        re.compile(r"\bi\s+(?:will|'ll|am\s+going\s+to)\s+kill\s+you\b", re.I),
        re.compile(r"\bmass\s+shooting\b", re.I),
    ),
    ModerationCategory.HATE_HARASSMENT: (
        re.compile(r"\ball\s+\w+\s+(?:people\s+)?(?:are|should\s+be)\s+(?:killed|exterminated|eliminated)\b", re.I),
        re.compile(r"\byou'?re\s+(?:a\s+)?(?:worthless|subhuman)\b", re.I),
    ),
    ModerationCategory.SEXUAL: (
        re.compile(r"\bsexually\s+explicit\s+(?:content|description|story)\b", re.I),
        re.compile(r"\bchild\s+sexual\s+abuse\b", re.I),
    ),
}


def classify(text: str) -> set[ModerationCategory]:
    """返回文本命中的所有类别；空集合表示未命中规则。"""
    hits: set[ModerationCategory] = set()
    for category, patterns in _PATTERNS.items():
        if any(p.search(text) for p in patterns):
            hits.add(category)
    return hits
