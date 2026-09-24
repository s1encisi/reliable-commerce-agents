"""净化不可信文本中的存储型和间接提示注入。

评论、描述或订单备注可能包含忽略指令、角色伪造、虚假系统轮次或
窃取提示词的内容。用 [neutralized] 替换相关标记，保留文本曾存在的
证据，并移除用于隐藏指令的控制字符和零宽字符。

纯函数，无 I/O、模型或数据库访问；规则偏重低误报，与提示词规则
及输入注入检测组成多层防护。
"""

from __future__ import annotations

import re
from typing import Any

# 移除 C0 控制符，但保留 TAB、LF 和 CR；
# 另移除 DEL、零宽字符、行段分隔符及 BOM。
# 通过数字码点和 translate 声明，
# 避免源码直接包含控制字节。
_STRIP_CODEPOINTS = (
    *range(0x00, 0x09),
    0x0B,
    0x0C,
    *range(0x0E, 0x20),
    0x7F,
    *range(0x200B, 0x2010),
    0x2028,
    0x2029,
    0xFEFF,
)
_CONTROL_TRANSLATION = dict.fromkeys(_STRIP_CODEPOINTS)

# 高精度注入信号，不区分大小写；伪造轮次规则锚定行首，
# 避免误伤普通正文，
# 例如商品名称中的 System: Pro。
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+"
        r"(?:instructions?|prompts?|rules?|messages?)",
        re.I,
    ),
    re.compile(
        r"disregard\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+"
        r"(?:instructions?|prompts?|rules?)",
        re.I,
    ),
    re.compile(
        r"forget\s+(?:all\s+|everything\s+|your\s+)?(?:previous\s+|prior\s+)?"
        r"(?:instructions?|rules?)",
        re.I,
    ),
    re.compile(r"you\s+are\s+now\s+(?:a|an|the)\b", re.I),
    re.compile(r"new\s+(?:system\s+)?(?:instructions?|prompts?|rules?)\s*:", re.I),
    re.compile(r"(?m)^\s{0,6}(?:system|developer|assistant)\s*:", re.I),
    re.compile(r"reveal\s+(?:your\s+|the\s+)?(?:system\s+)?(?:prompt|instructions?)", re.I),
    re.compile(r"</?\s*(?:system|instructions?|prompt)\s*>", re.I),
    re.compile(r"\bact\s+as\s+(?:if\s+you\s+are\s+)?(?:an?\s+)?admin", re.I),
)

_MARK = "[neutralized]"


def contains_injection_markers(text: str) -> bool:
    """文本命中任一高精度注入信号时返回 True。"""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def neutralize_text(text: str) -> str:
    """净化单个字符串的注入标记，并移除控制与零宽字符。"""
    if not text:
        return text
    cleaned = text.translate(_CONTROL_TRANSLATION)
    for pattern in _INJECTION_PATTERNS:
        cleaned = pattern.sub(_MARK, cleaned)
    return cleaned


def neutralize_value(value: Any, *, fields: set[str] | None = None, _key: str | None = None) -> Any:
    """递归净化工具结果中的不可信字符串。

    value 为字符串、字典、列表或标量。fields 指定时，只净化任意深度
    直属字典键命中的字符串；未指定则处理全部字符串。_key 为递归内部
    使用的当前字段名。
    """
    if isinstance(value, str):
        if fields is None or _key in fields:
            return neutralize_text(value)
        return value
    if isinstance(value, dict):
        return {k: neutralize_value(v, fields=fields, _key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [neutralize_value(v, fields=fields, _key=_key) for v in value]
    if isinstance(value, tuple):
        return tuple(neutralize_value(v, fields=fields, _key=_key) for v in value)
    return value
