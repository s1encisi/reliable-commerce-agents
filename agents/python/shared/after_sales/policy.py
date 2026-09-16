"""returns-v1: a continuous, inclusive 30-day window with trusted evidence."""

from datetime import UTC, datetime, timedelta

from shared.after_sales.contracts import Outcome, ReturnDecision, ReturnSnapshot

POLICY_VERSION = "returns-v1"
RETURN_WINDOW = timedelta(days=30)
APPROVAL_TTL = timedelta(hours=24)


def aware(value: datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() is not None


def evaluate_return(snapshot: ReturnSnapshot, *, now: datetime) -> ReturnDecision:
    """No I/O or implicit clock. Duplicate events with the same timestamp agree."""
    if not aware(now):
        raise ValueError("Return policy requires an aware clock")
    if snapshot.existing_return_id is not None:
        return ReturnDecision(Outcome.REJECTED, "RETURN_EXISTS", "A return already exists for this order.")
    if snapshot.status != "delivered":
        return ReturnDecision(Outcome.REJECTED, "ORDER_NOT_DELIVERED", "Only delivered orders can be returned.")
    if not snapshot.delivery_times:
        return ReturnDecision(Outcome.NEEDS_REVIEW, "DELIVERY_TIME_MISSING", "Delivery time needs human verification.")
    if not aware(snapshot.created_at) or any(not aware(t) for t in snapshot.delivery_times):
        return ReturnDecision(
            Outcome.NEEDS_REVIEW, "DELIVERY_TIME_INVALID", "Delivery evidence needs human verification."
        )
    times = {t.astimezone(UTC) for t in snapshot.delivery_times if t is not None}
    if len(times) != 1:
        return ReturnDecision(
            Outcome.NEEDS_REVIEW, "DELIVERY_TIME_CONFLICT", "Delivery records disagree; review is needed."
        )
    delivered_at = next(iter(times))
    if delivered_at > now or delivered_at < snapshot.created_at:
        return ReturnDecision(
            Outcome.NEEDS_REVIEW, "DELIVERY_TIME_INVALID", "Delivery time conflicts with the order timeline."
        )
    deadline = delivered_at + RETURN_WINDOW
    if now > deadline:
        return ReturnDecision(
            Outcome.REJECTED, "RETURN_WINDOW_EXPIRED", "The 30-day return window has expired.", delivered_at, deadline
        )
    return ReturnDecision(
        Outcome.READY, "RETURN_ELIGIBLE", "Order is eligible for a return request.", delivered_at, deadline
    )
