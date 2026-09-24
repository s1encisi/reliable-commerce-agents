"""解包 FunctionInvocationContext.result，得到工具原始 Python 返回值。

MAF 运行时将 @tool 结果包装为 list[Content]，其中 text 保存 JSON。
若中间件只按裸字典处理，会静默跳过事实记录和输出净化。直接传字典的
单元测试无法暴露此差异，因此事实台账和护栏都通过本辅助函数读取结果。
"""

from __future__ import annotations

import json
from typing import Any


def unwrap_function_result(result: Any) -> Any:
    """返回工具真正的结果。

    list[Content] 解析首项 text 中的 JSON；裸字典、列表或标量保持原样，
    兼容直接单元测试和不包装结果的调用方。
    """
    if isinstance(result, list) and result and hasattr(result[0], "text"):
        text = getattr(result[0], "text", None)
        if not text:
            return None
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return text
    return result


def rewrap_function_result(original: Any, new_value: Any) -> Any:
    """将新值写回原有容器形态，是 unwrap_function_result 的逆操作。

    中间件不能把运行时 list[Content] 替换成裸字典；应原地修改可变的
    Content.text，保持后续处理链需要的包装类型。
    """
    if isinstance(original, list) and original and hasattr(original[0], "text"):
        original[0].text = json.dumps(new_value)
        return original
    return new_value
