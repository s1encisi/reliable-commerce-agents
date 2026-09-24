"""业务决策与现有的 orders/returns 状态相互独立。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class Outcome(StrEnum):
    READY = "READY"
    NEEDS_INPUT = "NEEDS_INPUT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REJECTED = "REJECTED"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    UNKNOWN = "UNKNOWN"
    FAILED_FINAL = "FAILED_FINAL"


@dataclass(frozen=True)
class ReturnSnapshot:
    order_id: UUID
    user_id: UUID
    status: str
    total: Decimal
    created_at: datetime | None
    delivery_times: tuple[datetime | None, ...]
    existing_return_id: UUID | None = None


@dataclass(frozen=True)
class ReturnDecision:
    outcome: Outcome
    code: str
    message: str
    delivered_at: datetime | None = None
    deadline: datetime | None = None

    @property
    def eligible(self) -> bool:
        return self.outcome == Outcome.READY
