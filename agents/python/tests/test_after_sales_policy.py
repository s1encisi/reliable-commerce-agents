"""Deterministic, exact-time policy boundaries; no implicit wall clock."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from shared.after_sales.contracts import Outcome, ReturnSnapshot
from shared.after_sales.policy import evaluate_return

NOW = datetime(2026, 9, 15, 12, tzinfo=UTC)


def snapshot(*, days: int = 10) -> ReturnSnapshot:
    return ReturnSnapshot(
        uuid4(), uuid4(), "delivered", Decimal("49.99"), NOW - timedelta(days=60), (NOW - timedelta(days=days),)
    )


@pytest.mark.parametrize("offset,eligible", [(-1, True), (0, True), (1, False)])
def test_inclusive_window_compares_exact_seconds(offset: int, eligible: bool) -> None:
    result = evaluate_return(snapshot(days=30), now=NOW + timedelta(seconds=offset))
    assert result.eligible is eligible


@pytest.mark.parametrize(
    "delivery_times,code",
    [
        ((), "DELIVERY_TIME_MISSING"),
        ((None,), "DELIVERY_TIME_INVALID"),
        ((NOW.replace(tzinfo=None),), "DELIVERY_TIME_INVALID"),
        ((NOW + timedelta(seconds=1),), "DELIVERY_TIME_INVALID"),
        ((NOW - timedelta(days=61),), "DELIVERY_TIME_INVALID"),
        ((NOW - timedelta(days=1), NOW - timedelta(days=2)), "DELIVERY_TIME_CONFLICT"),
    ],
)
def test_missing_or_conflicting_evidence_needs_review(
    delivery_times: tuple[datetime | None, ...],
    code: str,
) -> None:
    result = evaluate_return(replace(snapshot(), delivery_times=delivery_times), now=NOW)
    assert result.outcome == Outcome.NEEDS_REVIEW
    assert result.code == code


def test_equivalent_events_in_different_timezones_agree() -> None:
    delivered = NOW - timedelta(days=5)
    times = (delivered, delivered.astimezone(timezone(timedelta(hours=8))))
    assert evaluate_return(replace(snapshot(), delivery_times=times), now=NOW).eligible


@pytest.mark.parametrize("status", ["placed", "shipped", "cancelled", "returned"])
def test_non_delivered_orders_are_rejected(status: str) -> None:
    result = evaluate_return(replace(snapshot(), status=status), now=NOW)
    assert result.code == "ORDER_NOT_DELIVERED"


def test_existing_return_wins_over_order_status() -> None:
    result = evaluate_return(replace(snapshot(), status="returned", existing_return_id=uuid4()), now=NOW)
    assert result.code == "RETURN_EXISTS"


def test_naive_clock_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="aware clock"):
        evaluate_return(snapshot(), now=NOW.replace(tzinfo=None))
