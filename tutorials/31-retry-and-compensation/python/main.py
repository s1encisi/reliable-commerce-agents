"""
MAF v1 — 第 31 章：重试与补偿（Saga 模式）（Python）

不涉及 LLM —— Saga 模式是纯粹的编排逻辑，不是智能体推理。三个内存「服务」
（库存、支付、发货）代表单个数据库事务永远无法跨越的独立 API / DB 调用。
每一步都配有明确的补偿动作，在后续步骤失败时撤销它，从而让半途而废的订单
干净地回滚，而不是留下孤儿状态。

运行：
    python tutorials/31-retry-and-compensation/python/main.py
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

# ─────────────── 错误类型 ───────────────
#
# 下面的区分正是本章的全部要点：TransientError 值得重试（一次网络抖动，
# 下一次尝试大概率就成功了）；其他一切都是真实失败，必须立即触发补偿，
# 而不是反复捶打一个靠自身永远不可能成功的调用。


class TransientError(Exception):
    """可重试的临时故障 —— 例如与服务通信时网络超时。"""


class OutOfStockError(Exception):
    """真实失败。重试不会凭空变出并不存在的库存。"""


class PaymentDeclinedError(Exception):
    """真实失败。重试不会把被拒的卡变成通过的卡。"""


# ─────────────── 内存后端（真实服务的替身） ───────────────


class Backends:
    """三个独立服务（库存、支付、发货）的玩具级内存替身。真实的 saga
    在这里会调用三个彼此独立的 API 或数据库 —— 它们之间没有任何一个
    共享事务。
    """

    def __init__(self, *, reserve_stock_flaky_calls: int = 0) -> None:
        self.stock: dict[str, int] = {"widget": 10, "gadget": 0}
        self.reservations: dict[str, int] = {}
        self.payments: dict[str, float] = {}
        self.shipments: dict[str, str] = {}
        # 模拟一次对库存服务的抖动网络调用：前 `reserve_stock_flaky_calls`
        # 次调用抛 TransientError，之后恢复正常。用来在演示中展示一次
        # 最终成功的重试。
        self._reserve_flaky_calls = reserve_stock_flaky_calls
        self._reserve_attempts = 0


# ─────────────── 步骤动作 ───────────────


def reserve_stock(backends: Backends, product_id: str, qty: int) -> None:
    backends._reserve_attempts += 1
    if backends._reserve_attempts <= backends._reserve_flaky_calls:
        raise TransientError(f"inventory service timed out (attempt {backends._reserve_attempts})")
    available = backends.stock.get(product_id, 0)
    if available < qty:
        raise OutOfStockError(f"only {available} '{product_id}' in stock, need {qty}")
    backends.stock[product_id] = available - qty
    backends.reservations[product_id] = backends.reservations.get(product_id, 0) + qty


def charge_payment(backends: Backends, order_id: str, amount: float, *, should_fail: bool = False) -> None:
    if should_fail:
        raise PaymentDeclinedError(f"payment declined for order {order_id}")
    backends.payments[order_id] = amount


def create_shipment(backends: Backends, order_id: str, *, should_fail: bool = False) -> None:
    if should_fail:
        raise RuntimeError(f"shipment carrier rejected order {order_id}")
    backends.shipments[order_id] = "created"


# ─────────────── 补偿动作 ───────────────
#
# 每个补偿都是其对应动作的严格逆操作 —— 这就是 saga 契约。这三个服务
# 之间没有数据库回滚；这是撤销一张半途而废的订单的唯一办法。


def release_stock(backends: Backends, product_id: str, qty: int) -> None:
    backends.stock[product_id] = backends.stock.get(product_id, 0) + qty
    backends.reservations[product_id] = backends.reservations.get(product_id, 0) - qty


def refund_payment(backends: Backends, order_id: str) -> None:
    backends.payments.pop(order_id, None)


def cancel_shipment(backends: Backends, order_id: str) -> None:
    backends.shipments[order_id] = "cancelled"


# ─────────────── Saga 引擎 ───────────────


@dataclass
class SagaStep:
    name: str
    action: Callable[[], None]
    compensation: Callable[[], None]
    retryable: bool = False


@dataclass
class SagaResult:
    order_id: str
    succeeded: bool
    completed_steps: list[str] = field(default_factory=list)
    failed_step: str | None = None
    compensated_steps: list[str] = field(default_factory=list)


def _compensate(completed: list[SagaStep]) -> list[str]:
    """沿已完成步骤反向遍历，按逆序逐个撤销 —— 正是这次回退让 saga
    模式得以成立。
    """
    compensated: list[str] = []
    for step in reversed(completed):
        print(f"  [compensate] 正在撤销 {step.name}")
        step.compensation()
        compensated.append(step.name)
    return compensated


def run_saga(order_id: str, steps: list[SagaStep], *, max_attempts: int = 3, base_delay: float = 0.0) -> SagaResult:
    """按顺序执行一串 saga 步骤。

    标记了 `retryable=True` 的步骤在遇到 `TransientError` 时会以指数退避
    重试 —— 最多 `max_attempts` 次 —— 之后放弃。任何其他异常（真实失败，
    例如 `PaymentDeclinedError`）会立即触发补偿：重试一笔被拒的支付只是
    浪费时间，而且如果重试不是幂等的，甚至可能把客户重复扣款。
    """
    completed: list[SagaStep] = []
    for step in steps:
        attempt = 0
        while True:
            attempt += 1
            try:
                step.action()
            except TransientError as exc:
                if step.retryable and attempt < max_attempts:
                    delay = base_delay * (2 ** (attempt - 1))
                    print(f"  [retry] {step.name}: {exc}（第 {attempt}/{max_attempts} 次尝试，退避 {delay:.2f}s）")
                    if delay:
                        time.sleep(delay)
                    continue
                print(f"  [failed] {step.name}: {exc}（重试次数已耗尽）")
                compensated = _compensate(completed)
                return SagaResult(order_id, False, [s.name for s in completed], step.name, compensated)
            except Exception as exc:  # noqa: BLE001 - 真实失败，而非瞬时故障
                print(f"  [failed] {step.name}: {exc}（不可重试 —— 立即补偿）")
                compensated = _compensate(completed)
                return SagaResult(order_id, False, [s.name for s in completed], step.name, compensated)
            else:
                print(f"  [ok] {step.name}")
                completed.append(step)
                break
    print(f"  [done] 订单 {order_id} 下单成功")
    return SagaResult(order_id, True, [s.name for s in completed])


# ─────────────── 「下单」saga ───────────────


def build_place_order_saga(
    backends: Backends,
    order_id: str,
    product_id: str,
    qty: int,
    amount: float,
    *,
    fail_payment: bool = False,
    fail_shipment: bool = False,
) -> list[SagaStep]:
    return [
        SagaStep(
            name="reserve_stock",
            action=lambda: reserve_stock(backends, product_id, qty),
            compensation=lambda: release_stock(backends, product_id, qty),
            retryable=True,
        ),
        SagaStep(
            name="charge_payment",
            action=lambda: charge_payment(backends, order_id, amount, should_fail=fail_payment),
            compensation=lambda: refund_payment(backends, order_id),
        ),
        SagaStep(
            name="create_shipment",
            action=lambda: create_shipment(backends, order_id, should_fail=fail_shipment),
            compensation=lambda: cancel_shipment(backends, order_id),
        ),
    ]


def main() -> None:
    print("=== 场景 1：顺利路径 —— 三步全部成功 ===")
    backends = Backends()
    steps = build_place_order_saga(backends, "order-1", "widget", 2, 49.99)
    result = run_saga("order-1", steps)
    print(result)

    print("\n=== 场景 2：reserve_stock 上瞬时网络抖动，重试后成功 ===")
    backends = Backends(reserve_stock_flaky_calls=2)
    steps = build_place_order_saga(backends, "order-2", "widget", 1, 19.99)
    result = run_saga("order-2", steps, base_delay=0.01)
    print(result)

    print("\n=== 场景 3：支付被拒 —— 真实失败，回滚已预留的库存 ===")
    backends = Backends()
    steps = build_place_order_saga(backends, "order-3", "widget", 3, 99.99, fail_payment=True)
    result = run_saga("order-3", steps)
    print(result)


if __name__ == "__main__":
    main()
