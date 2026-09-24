"""通过 MAF 函数中间件记录智能体执行时间线。

每个智能体都挂载 StepRecorderMiddleware，工具调用结束后把工具名、参数、
状态、耗时和简短输出写入请求级 current_steps。编排器读取这些步骤，
持久化到 agent_execution_steps、填充 messages.metadata，并发送 step SSE 帧。

专业智能体在独立进程运行；宿主重置列表、标记智能体名称，再通过 A2A
返回步骤，供编排器合并。current_steps 为 None 时中间件不做记录，
因此可以无条件挂载。
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

# 直接从子模块导入，保持与早期 MAF 包的兼容性。
# 早期 1.0 beta 的 __init__.py 为空，
# Docker 构建曾通过 patch_maf.py 修补导出，
# 具体子模块路径也适用于未经修补的本地检出。
from agent_framework._middleware import FunctionInvocationContext, FunctionMiddleware

from shared.context import current_steps

_MAX = 600


def _extract_row_ids(result: Any) -> list[str]:
    """尽可能提取工具结果中的标识，用于步骤溯源。

    按数据形态识别商品的 id、订单的 order_id 与库存的 product_id。
    与事实台账的识别方式一致，但在本模块独立实现，避免通用可观测性
    模块依赖事实核验子系统。
    """
    ids: list[str] = []
    items = result if isinstance(result, list) else [result]
    for item in items:
        if not isinstance(item, dict):
            continue
        row_id = item.get("id") or item.get("order_id") or item.get("product_id")
        if row_id:
            ids.append(str(row_id))
    return ids


def _summarize(value: Any, limit: int = _MAX) -> Any:
    """生成可 JSON 序列化且限制大小的工具输入输出摘要。"""
    if value is None:
        return None
    try:
        s = json.dumps(value, default=str)
    except (TypeError, ValueError):
        s = str(value)
    if len(s) > limit:
        return s[:limit] + "…"
    # 小型 JSON 往返序列化，尽量让界面得到结构化数据。
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return s


class StepRecorderMiddleware(FunctionMiddleware):
    """每次工具调用向 current_steps 记录一个时间线步骤。"""

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        tool_name = getattr(getattr(context, "function", None), "name", "tool")
        args = getattr(context, "arguments", None)
        try:
            tool_input = dict(args) if args is not None else {}
        except (TypeError, ValueError):
            tool_input = {"args": str(args)}

        start = time.perf_counter()
        status = "success"
        try:
            await call_next()
        except Exception:
            status = "error"
            raise
        finally:
            steps = current_steps.get()
            if steps is not None:
                from shared.function_results import unwrap_function_result

                result = unwrap_function_result(getattr(context, "result", None))
                confirmed = bool(result) and isinstance(result, (dict, list))
                if isinstance(result, dict) and (
                    result.get("error") or result.get("error_code") or result.get("success") is False
                ):
                    confirmed = False
                if isinstance(result, list) and any(isinstance(x, dict) and x.get("error") for x in result):
                    confirmed = False
                from shared.after_sales.contracts import Outcome

                outcome = result.get("outcome") if isinstance(result, dict) else None
                steps.append(
                    {
                        "tool_name": tool_name,
                        "tool_input": _summarize(tool_input),
                        "tool_output": _summarize(result, 400),
                        "result_confirmed": confirmed,
                        "status": status,
                        "business_outcome": outcome
                        if isinstance(outcome, str) and outcome in {s.value for s in Outcome}
                        else None,
                        "business_success": result.get("success")
                        if isinstance(result, dict) and isinstance(result.get("success"), bool)
                        else None,
                        "duration_ms": int((time.perf_counter() - start) * 1000),
                        "provenance": {"source": f"tool:{tool_name}", "row_ids": _extract_row_ids(result)},
                    }
                )


# 中间件自身无状态；所有状态都保存在请求级 ContextVar 中，
# 因此所有智能体可以复用同一个实例。
STEP_MIDDLEWARE: list[FunctionMiddleware] = [StepRecorderMiddleware()]


def reset_steps() -> list[dict]:
    """开始记录当前请求，返回新建的步骤列表。"""
    fresh: list[dict] = []
    current_steps.set(fresh)
    return fresh


def get_steps() -> list[dict]:
    """返回本请求捕获的步骤；未启用记录时返回空列表。"""
    return current_steps.get() or []
