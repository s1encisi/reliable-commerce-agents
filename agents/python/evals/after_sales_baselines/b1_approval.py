"""内部授权快照；永远不会作为工具或 HTTP 参数被接受。"""

import hashlib
import json
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from shared.after_sales.contracts import ReturnSnapshot
from shared.after_sales.policy import APPROVAL_TTL, POLICY_VERSION, aware
from shared.tool_inputs import InitiateReturnInput


def payload_hash(request: InitiateReturnInput) -> str:
    data = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def order_revision(snapshot: ReturnSnapshot) -> str:
    data = json.dumps(
        {
            "order_id": str(snapshot.order_id),
            "user_id": str(snapshot.user_id),
            "status": snapshot.status,
            "total": str(snapshot.total),
            "created_at": str(snapshot.created_at),
            "delivery_times": sorted({str(t) for t in snapshot.delivery_times}),
        },
        sort_keys=True,
    )
    return hashlib.sha256(data.encode()).hexdigest()


@dataclass(frozen=True)
class ReturnApproval:
    user_email: str
    payload_hash: str
    order_revision: str
    policy_version: str
    expires_at: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReturnApproval":
        return cls(**value)

    def valid_for(self, request: InitiateReturnInput, snapshot: ReturnSnapshot, email: str, now: datetime) -> bool:
        try:
            expires_at = datetime.fromisoformat(self.expires_at)
        except (ValueError, TypeError):
            return False
        return (
            aware(expires_at)
            and now <= expires_at
            and self.user_email == email
            and self.policy_version == POLICY_VERSION
            and self.payload_hash == payload_hash(request)
            and self.order_revision == order_revision(snapshot)
        )


def bind_approval(request: InitiateReturnInput, snapshot: ReturnSnapshot, email: str, now: datetime) -> ReturnApproval:
    return ReturnApproval(
        email, payload_hash(request), order_revision(snapshot), POLICY_VERSION, (now + APPROVAL_TTL).isoformat()
    )


current_return_approval: ContextVar[ReturnApproval | None] = ContextVar("current_return_approval", default=None)
