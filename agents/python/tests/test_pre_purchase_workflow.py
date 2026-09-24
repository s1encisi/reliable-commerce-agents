"""购前并发工作流测试，替换工具函数，验证扇出、汇聚与综合链路，无模型或外部服务。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from workflows.pre_purchase import PrePurchaseWorkflow, ResearchState

# ─────────────────────── Tool stubs ───────────────────────


async def _sentiment_ok(product_id: str) -> dict[str, Any]:
    return {"overall_sentiment": "positive", "average_rating": 4.4}


async def _stock_ok(product_id: str) -> dict[str, Any]:
    return {"in_stock": True, "total_quantity": 17}


async def _stock_out(product_id: str) -> dict[str, Any]:
    return {"in_stock": False, "total_quantity": 0}


async def _price_good(product_id: str, days: int) -> dict[str, Any]:
    return {"is_good_deal": True, "average_price": 120.5, "trend": "flat"}


async def _shipping_fast(product_id: str, destination_region: str) -> dict[str, Any]:
    return {
        "shipping_options": [
            {"price": 4.99, "delivery_window": "2 business days"},
            {"price": 12.99, "delivery_window": "1 business day"},
        ]
    }


# ─────────────────────── Happy path ───────────────────────


@pytest.mark.asyncio
async def test_all_three_parallel_branches_populate_state() -> None:
    tools = {
        "analyze_sentiment": _sentiment_ok,
        "check_stock": _stock_ok,
        "get_price_history": _price_good,
        "estimate_shipping": _shipping_fast,
    }
    state = await PrePurchaseWorkflow(tools).execute(ResearchState(product_id="sku-1"))

    assert state.reviews == {"overall_sentiment": "positive", "average_rating": 4.4}
    assert state.stock == {"in_stock": True, "total_quantity": 17}
    assert state.price_history["is_good_deal"] is True
    assert state.shipping["shipping_options"]
    assert set(state.completed_steps) >= {"reviews", "stock", "price_history", "shipping"}
    assert state.errors == []


@pytest.mark.asyncio
async def test_recommendation_includes_all_signals() -> None:
    tools = {
        "analyze_sentiment": _sentiment_ok,
        "check_stock": _stock_ok,
        "get_price_history": _price_good,
        "estimate_shipping": _shipping_fast,
    }
    state = await PrePurchaseWorkflow(tools).execute(ResearchState(product_id="sku-1"))
    rec = state.recommendation

    assert "Reviews: positive" in rec
    assert "17 units" in rec
    assert "Good deal" in rec
    assert "$4.99" in rec


# ─────────────────────── Out-of-stock branch ───────────────


@pytest.mark.asyncio
async def test_shipping_skipped_when_out_of_stock() -> None:
    tools = {
        "analyze_sentiment": _sentiment_ok,
        "check_stock": _stock_out,
        "get_price_history": _price_good,
        "estimate_shipping": _shipping_fast,
    }
    state = await PrePurchaseWorkflow(tools).execute(ResearchState(product_id="sku-2"))

    assert state.stock["in_stock"] is False
    assert state.shipping == {}
    assert "shipping" not in state.completed_steps
    assert "Stock: Currently out of stock" in state.recommendation


# ─────────────────────── Tool failures ───────────────────────


@pytest.mark.asyncio
async def test_single_branch_failure_does_not_abort_run() -> None:
    """一个工具失败写入 state.errors，其他分支和综合仍应完成。"""

    async def _boom(**_: Any) -> dict[str, Any]:
        raise RuntimeError("sentiment service down")

    tools = {
        "analyze_sentiment": _boom,
        "check_stock": _stock_ok,
        "get_price_history": _price_good,
        "estimate_shipping": _shipping_fast,
    }
    state = await PrePurchaseWorkflow(tools).execute(ResearchState(product_id="sku-3"))

    assert any("reviews: sentiment service down" in e for e in state.errors)
    # 其他分支仍填充各自结果。
    assert state.stock["in_stock"] is True
    assert state.price_history["is_good_deal"] is True
    # 综合仍给出建议，并说明缺失信息。
    assert state.recommendation


@pytest.mark.asyncio
async def test_missing_tools_produce_recommendation_gaps() -> None:
    """分支无工具时状态为空，建议中应明确缺口。"""
    state = await PrePurchaseWorkflow(tools={}).execute(ResearchState(product_id="sku-4"))
    assert state.reviews == {}
    assert state.stock == {}
    assert state.price_history == {}
    assert "Currently out of stock" in state.recommendation


# ─────────────────────── Parallelism proof ──────────────────


@pytest.mark.asyncio
async def test_three_branches_actually_run_in_parallel() -> None:
    """并发耗时检查：三个分支各等待 0.3 秒，串行约 0.9 秒，并发约 0.3 秒。"""

    async def _slow_sentiment(product_id: str) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {"overall_sentiment": "ok", "average_rating": 3.0}

    async def _slow_stock(product_id: str) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {"in_stock": True, "total_quantity": 1}

    async def _slow_price(product_id: str, days: int) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {"is_good_deal": False}

    tools = {
        "analyze_sentiment": _slow_sentiment,
        "check_stock": _slow_stock,
        "get_price_history": _slow_price,
    }

    start = asyncio.get_event_loop().time()
    await PrePurchaseWorkflow(tools).execute(ResearchState(product_id="sku-5"))
    elapsed = asyncio.get_event_loop().time() - start

    # 并发应明显快于 0.9 秒串行基线，保留调度余量。
    assert elapsed < 0.8, f"Expected parallel execution (<0.8s), got {elapsed:.3f}s"


# ─────────────────────── Workflow structure ─────────────────


def test_workflow_builder_wires_every_executor() -> None:
    wf = PrePurchaseWorkflow(tools={})._build_maf_workflow()
    ids = {getattr(e, "id", None) for e in wf.get_executors_list()}
    assert {"fan-out", "reviews", "stock", "price-history", "merge-and-ship", "synthesis"} <= ids


# ─────────────── Partial results must look partial (plan 19 §2b) ───────────────
#
# 历史故障中，多分支工作流只返回极短摘要，
# 虽然执行拓扑正确，
# 但综合每一行都依赖相应数据，
# 输入缺失却没有说明。
#
# 缺工具时无错误、无步骤记录，
# 已有 state.errors 也未被读取，
# 回答无法区分查无结果和从未执行。
# 以下用例分别固定这三个边界。


async def test_a_missing_tool_is_recorded_rather_than_skipped_silently() -> None:
    """工具缺失必须显式记录，不能与已执行但无结果混淆。"""

    # 只接入库存，其余三个工具缺失。
    async def _stock(product_id: str) -> dict:
        return {"in_stock": True, "total_quantity": 5}

    workflow = PrePurchaseWorkflow({"check_stock": _stock})
    state = await workflow.execute(ResearchState(product_id="p1"))

    assert "stock" in state.completed_steps
    joined = " ".join(state.errors)
    assert "analyze_sentiment" in joined
    assert "get_price_history" in joined
    assert "estimate_shipping" in joined


async def test_the_recommendation_names_what_it_could_not_check() -> None:
    """建议应说明缺少哪些信息，避免让不完整回答显得全面。"""

    async def _stock(product_id: str) -> dict:
        return {"in_stock": True, "total_quantity": 348}

    async def _price(product_id: str, days: int) -> dict:
        return {"trend": "stable"}

    workflow = PrePurchaseWorkflow({"check_stock": _stock, "get_price_history": _price})
    state = await workflow.execute(ResearchState(product_id="p1"))

    # 复现原始缺陷形态，当前应提供明确缺口说明。
    assert "Stock: 348 units available" in state.recommendation
    assert "could not check" in state.recommendation
    assert "reviews" in state.recommendation
    assert "shipping" in state.recommendation


async def test_a_failing_tool_is_recorded_and_does_not_take_the_run_down() -> None:
    async def _boom(product_id: str) -> dict:
        raise RuntimeError("sentiment service unavailable")

    async def _stock(product_id: str) -> dict:
        return {"in_stock": True, "total_quantity": 1}

    workflow = PrePurchaseWorkflow({"analyze_sentiment": _boom, "check_stock": _stock})
    state = await workflow.execute(ResearchState(product_id="p1"))

    assert any("sentiment service unavailable" in e for e in state.errors)
    assert "reviews" not in state.completed_steps
    assert state.recommendation, "one dead probe must not lose the whole answer"


async def test_all_four_contributions_produce_no_caveat() -> None:
    """对照用例：所有查询都正常返回时，不应输出多余的无法检查提示。"""

    async def _reviews(product_id: str) -> dict:
        return {"overall_sentiment": "positive", "average_rating": 4.6}

    async def _stock(product_id: str) -> dict:
        return {"in_stock": True, "total_quantity": 317}

    async def _price(product_id: str, days: int) -> dict:
        return {"trend": "stable"}

    async def _shipping(product_id: str, destination_region: str) -> dict:
        return {"shipping_options": [{"price": 5.99, "delivery_window": "5-7 business days"}]}

    workflow = PrePurchaseWorkflow(
        {
            "analyze_sentiment": _reviews,
            "check_stock": _stock,
            "get_price_history": _price,
            "estimate_shipping": _shipping,
        }
    )
    state = await workflow.execute(ResearchState(product_id="p1"))

    assert "could not check" not in state.recommendation
    assert state.errors == []
    for step in ("reviews", "stock", "price_history", "shipping"):
        assert step in state.completed_steps


async def test_out_of_stock_records_why_shipping_was_skipped() -> None:
    """缺货时不估算配送属于正常分支，但要与未执行查询区分。"""

    async def _stock(product_id: str) -> dict:
        return {"in_stock": False, "total_quantity": 0}

    async def _shipping(product_id: str, destination_region: str) -> dict:
        raise AssertionError("shipping must not be called for an out-of-stock product")

    workflow = PrePurchaseWorkflow({"check_stock": _stock, "estimate_shipping": _shipping})
    state = await workflow.execute(ResearchState(product_id="p1"))

    assert any("out of stock" in e for e in state.errors)
    assert "Currently out of stock" in state.recommendation


async def test_a_probe_that_runs_but_returns_nothing_usable_is_still_reported() -> None:
    """工具运行成功仍可能返回空字典或缺关键字段。

    completed_steps 只能证明执行过，不能证明结果足以支持建议。
    """
    from workflows.pre_purchase import PrePurchaseWorkflow, ResearchState

    async def _stock(product_id: str) -> dict:
        return {"in_stock": True, "total_quantity": 348}

    async def _price(product_id: str, days: int) -> dict:
        return {"trend": "stable"}

    # 两个分支都成功执行，但都没返回可供建议使用的信息。
    async def _reviews_empty(product_id: str) -> dict:
        return {}

    async def _shipping_empty(product_id: str, destination_region: str) -> dict:
        return {"shipping_options": []}

    workflow = PrePurchaseWorkflow(
        {
            "analyze_sentiment": _reviews_empty,
            "check_stock": _stock,
            "get_price_history": _price,
            "estimate_shipping": _shipping_empty,
        }
    )
    state = await workflow.execute(ResearchState(product_id="p1"))

    # 步骤列表包含它们，
    # 所以缺口说明不能只由步骤完成状态推导。
    assert "reviews" in state.completed_steps
    assert "shipping" in state.completed_steps

    assert "could not check" in state.recommendation
    assert "reviews" in state.recommendation
    assert "shipping" in state.recommendation


def test_the_recommendation_reads_the_keys_the_tools_actually_return() -> None:
    """验证综合逻辑与真实工具字段契约一致。

    旧代码读取 sentiment/options，而工具实际返回 overall_sentiment/
    shipping_options；守卫会静默省略段落。测试对照工具源码，
    防止替身和消费方同时写错而仍然通过。
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]

    def returned_keys(path: str, func: str) -> set[str]:
        tree = ast.parse((root / path).read_text())
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == func)
        return {
            key.value
            for node in ast.walk(function)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }

    sentiment_keys = returned_keys("review_sentiment/tools.py", "analyze_sentiment")
    shipping_keys = returned_keys("inventory_fulfillment/tools.py", "estimate_shipping")

    assert "overall_sentiment" in sentiment_keys, (
        "analyze_sentiment renamed its sentiment field — _build_recommendation reads "
        "overall_sentiment and will silently stop emitting the reviews line"
    )
    assert "shipping_options" in shipping_keys, (
        "estimate_shipping renamed its options field — _build_recommendation reads "
        "shipping_options and will silently stop emitting the shipping line"
    )
