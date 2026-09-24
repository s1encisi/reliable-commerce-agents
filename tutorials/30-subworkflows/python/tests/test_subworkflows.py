"""
第 30 章 —— 子工作流：测试。

不涉及 LLM —— 内层与外层工作流都是在玩具级目录之上的确定性图逻辑，
因此每条断言都是精确的（与第 09 章的无 LLM 工作流测试同样先例）。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    ReplacementResult,
    WorkflowExecutor,
    build_find_replacement_workflow,
    build_process_return_workflow,
    run_find_replacement,
    run_process_return,
)

# ─────────────── 内层工作流，单独运行 ───────────────


@pytest.mark.asyncio
async def test_inner_workflow_approves_in_stock_catalog_item() -> None:
    result = await run_find_replacement("R-9001", "sku-mug-red")
    assert isinstance(result, ReplacementResult)
    assert result.approved is True
    assert "in stock" in result.reason


@pytest.mark.asyncio
async def test_inner_workflow_rejects_out_of_stock_item() -> None:
    result = await run_find_replacement("R-9002", "sku-mug-blue")
    assert isinstance(result, ReplacementResult)
    assert result.approved is False
    assert result.reason == "out of stock"


@pytest.mark.asyncio
async def test_inner_workflow_rejects_unknown_product() -> None:
    result = await run_find_replacement("R-9003", "sku-does-not-exist")
    assert isinstance(result, ReplacementResult)
    assert result.approved is False
    assert result.reason == "not found in catalog"


@pytest.mark.asyncio
async def test_inner_workflow_wires_all_three_executors() -> None:
    workflow = build_find_replacement_workflow()
    ids = {getattr(e, "id", None) for e in workflow.get_executors_list()}
    assert {"validate_catalog", "check_stock", "approve"} <= ids


# ─────────────── 外层工作流，端到端（途经嵌套的内层工作流） ───────────────


@pytest.mark.asyncio
async def test_outer_workflow_approves_replacement_end_to_end() -> None:
    outputs = await run_process_return("R-1001", "sku-mug-red")
    assert len(outputs) == 1
    assert "approved and shipped" in outputs[0]
    assert "R-1001" in outputs[0]


@pytest.mark.asyncio
async def test_outer_workflow_rejects_out_of_stock_replacement_end_to_end() -> None:
    outputs = await run_process_return("R-1002", "sku-mug-blue")
    assert len(outputs) == 1
    assert "rejected" in outputs[0]
    assert "out of stock" in outputs[0]
    assert "refund" in outputs[0]


@pytest.mark.asyncio
async def test_outer_workflow_rejects_unknown_product_end_to_end() -> None:
    outputs = await run_process_return("R-1003", "sku-unknown")
    assert len(outputs) == 1
    assert "rejected" in outputs[0]
    assert "not found in catalog" in outputs[0]


@pytest.mark.asyncio
async def test_outer_workflow_wires_a_workflow_executor_node_for_the_inner_workflow() -> None:
    """外层图的中间节点是一个包装了独立 Workflow 实例的 WorkflowExecutor ——
    这才是本章真正要教的组合机制，而不只是比对输出文本。"""
    workflow = build_process_return_workflow()
    executors = {getattr(e, "id", None): e for e in workflow.get_executors_list()}
    assert {"receive_return", "find_replacement", "finalize_return"} <= executors.keys()

    nested = executors["find_replacement"]
    assert isinstance(nested, WorkflowExecutor)
    assert nested.workflow.id != workflow.id


@pytest.mark.asyncio
async def test_two_outer_runs_use_independent_inner_workflow_instances() -> None:
    """build_process_return_workflow() 是工厂而非单例 —— 每次调用都必须构建一个
    全新的内层工作流，这也符合 WorkflowExecutor 文档字符串中「勿共享实例」的警告。"""
    first = build_process_return_workflow()
    second = build_process_return_workflow()
    first_nested = {getattr(e, "id", None): e for e in first.get_executors_list()}["find_replacement"]
    second_nested = {getattr(e, "id", None): e for e in second.get_executors_list()}["find_replacement"]
    assert first_nested.workflow is not second_nested.workflow
