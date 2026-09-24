"""
第 31 章 —— 重试与补偿（Saga 模式）：测试。

不涉及 LLM —— saga 引擎是确定性的编排逻辑，因此这里每条断言都是精确的：
哪些步骤跑了、哪一步失败、哪些补偿被触发，以及它们的顺序。
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    Backends,
    OutOfStockError,
    PaymentDeclinedError,
    SagaStep,
    TransientError,
    build_place_order_saga,
    run_saga,
)

# ─────────────────── 顺利路径 ──────────────────


def test_happy_path_completes_all_steps_with_no_compensation() -> None:
    backends = Backends()
    steps = build_place_order_saga(backends, "order-1", "widget", 2, 49.99)
    result = run_saga("order-1", steps)

    assert result.succeeded is True
    assert result.completed_steps == ["reserve_stock", "charge_payment", "create_shipment"]
    assert result.failed_step is None
    assert result.compensated_steps == []

    # 真实副作用已落到三个后端上。
    assert backends.stock["widget"] == 8
    assert backends.reservations["widget"] == 2
    assert backends.payments["order-1"] == 49.99
    assert backends.shipments["order-1"] == "created"


# ─────────────────── 真实失败 -> 立即补偿 ──────────


def test_payment_declined_compensates_reserved_stock_only() -> None:
    backends = Backends()
    steps = build_place_order_saga(backends, "order-3", "widget", 3, 99.99, fail_payment=True)
    result = run_saga("order-3", steps)

    assert result.succeeded is False
    assert result.completed_steps == ["reserve_stock"]
    assert result.failed_step == "charge_payment"
    # 补偿反向遍历：只有 reserve_stock 已完成，所以只跑 release_stock。
    assert result.compensated_steps == ["reserve_stock"]

    # 库存预留已被完全撤销。
    assert backends.stock["widget"] == 10
    assert backends.reservations["widget"] == 0
    assert "order-3" not in backends.payments


def test_payment_declined_is_not_retried() -> None:
    """PaymentDeclinedError 必须在首次失败时就触发补偿 ——
    重试一张被拒的卡正是本章所警告的反模式。
    """
    backends = Backends()
    steps = build_place_order_saga(backends, "order-x", "widget", 1, 10.0, fail_payment=True)
    result = run_saga("order-x", steps, max_attempts=5)

    assert result.failed_step == "charge_payment"
    # 只有库存的补偿跑了；没有任何迹象表明 charge_payment 被尝试了不止一次
    # （没有重试记录可供检查，但被回滚的库存数证明了 saga 在首次失败后就停了）。
    assert backends.stock["widget"] == 10


def test_shipment_failure_unwinds_both_earlier_steps_in_reverse_order() -> None:
    backends = Backends()
    steps = build_place_order_saga(backends, "order-4", "widget", 1, 25.0, fail_shipment=True)
    result = run_saga("order-4", steps)

    assert result.succeeded is False
    assert result.completed_steps == ["reserve_stock", "charge_payment"]
    assert result.failed_step == "create_shipment"
    # 逆序：支付是在库存预留之后扣的，所以必须先退款再释放库存。
    assert result.compensated_steps == ["charge_payment", "reserve_stock"]

    assert backends.stock["widget"] == 10
    assert backends.reservations["widget"] == 0
    assert "order-4" not in backends.payments
    assert backends.shipments.get("order-4") is None


def test_out_of_stock_is_a_genuine_failure_not_retried() -> None:
    backends = Backends()
    steps = build_place_order_saga(backends, "order-5", "gadget", 1, 10.0)  # gadget 库存为 0
    result = run_saga("order-5", steps, max_attempts=5)

    assert result.failed_step == "reserve_stock"
    # 失败步骤之前没有任何步骤完成，因此无需补偿。
    assert result.completed_steps == []
    assert result.compensated_steps == []


# ─────────────────── 瞬时故障 -> 带退避重试 ────────────


def test_transient_failure_retries_then_succeeds() -> None:
    backends = Backends(reserve_stock_flaky_calls=2)
    steps = build_place_order_saga(backends, "order-2", "widget", 1, 19.99)
    result = run_saga("order-2", steps, max_attempts=3, base_delay=0.0)

    assert result.succeeded is True
    assert result.completed_steps == ["reserve_stock", "charge_payment", "create_shipment"]
    # 证实重试确实发生了：两次调用失败，第 3 次成功。
    assert backends._reserve_attempts == 3


def test_transient_failure_exhausts_retries_and_compensates() -> None:
    # 抖动程度超出 max_attempts 所能容忍的范围 —— 每次尝试都失败。
    backends = Backends(reserve_stock_flaky_calls=5)
    steps = build_place_order_saga(backends, "order-6", "widget", 1, 10.0)
    result = run_saga("order-6", steps, max_attempts=3, base_delay=0.0)

    assert result.succeeded is False
    assert result.failed_step == "reserve_stock"
    assert result.completed_steps == []
    assert result.compensated_steps == []
    assert backends._reserve_attempts == 3


def test_non_retryable_step_does_not_retry_on_transient_error() -> None:
    """只有标记了 retryable=True 的步骤才会被重试。通过对动作打猴子补丁，
    在一个不可重试的步骤（charge_payment）上强行制造 TransientError，
    确认 saga 会补偿而不是空转。
    """
    calls = {"count": 0}

    def flaky_non_retryable_action() -> None:
        calls["count"] += 1
        raise TransientError("在未标记为可重试的步骤上模拟一次瞬时抖动")

    steps = [
        SagaStep(
            name="reserve_stock",
            action=lambda: None,
            compensation=lambda: None,
        ),
        SagaStep(
            name="flaky_step",
            action=flaky_non_retryable_action,
            compensation=lambda: None,
            retryable=False,
        ),
    ]
    result = run_saga("order-7", steps, max_attempts=5)

    assert result.failed_step == "flaky_step"
    assert calls["count"] == 1  # 没有重试 —— 即使是 TransientError，retryable=False 也优先
    assert result.compensated_steps == ["reserve_stock"]


# ─────────────────── stdout 中可见的回滚 ────────────────────────────


def test_unwind_is_printed_in_reverse_order(capsys) -> None:
    backends = Backends()
    steps = build_place_order_saga(backends, "order-8", "widget", 1, 15.0, fail_shipment=True)
    run_saga("order-8", steps)

    out = capsys.readouterr().out
    compensate_lines = [line for line in out.splitlines() if "[compensate]" in line]
    assert len(compensate_lines) == 2
    assert "charge_payment" in compensate_lines[0]
    assert "reserve_stock" in compensate_lines[1]


# ─────────────────── 异常类型 ─────────────────────────────────────


def test_exception_types_are_distinguishable() -> None:
    assert issubclass(OutOfStockError, Exception)
    assert issubclass(PaymentDeclinedError, Exception)
    assert issubclass(TransientError, Exception)
    assert not issubclass(TransientError, PaymentDeclinedError)
    assert not issubclass(OutOfStockError, TransientError)
