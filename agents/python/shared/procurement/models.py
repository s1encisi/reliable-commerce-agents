"""采购任务的公开契约；模型只提交数据，不能提交执行代码或权限。"""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CAPABILITIES = ("search_candidates", "quote_options", "check_inventory", "review_evidence")
current_procurement_binding: ContextVar[dict | None] = ContextVar("procurement_binding", default=None)


class ProcurementSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    query: str = Field(min_length=1, max_length=2000)
    quantity: int = Field(ge=1, le=100, strict=True)
    alternative_count: int = Field(default=2, ge=1, le=2, strict=True)
    total_budget: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: Literal["CNY"] = "CNY"
    in_stock: Literal[True] = True
    min_rating: float | None = Field(default=None, ge=0, le=5)
    preferences: list[str] = Field(default_factory=list, max_length=20)
    budget_scope: Literal["goods"] = "goods"
    quote_policy: Literal["single_coupon_v1"] = "single_coupon_v1"


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    capability: Literal["search_candidates", "quote_options", "check_inventory", "review_evidence"]
    instruction: str = Field(default="", max_length=2000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=8)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[PlanStep] = Field(min_length=1, max_length=8)
    rationale: str = Field(default="", max_length=2000)
    source: Literal["deterministic_demo", "model"] = "model"

    @model_validator(mode="after")
    def validate_graph(self):
        ids = {step.id for step in self.steps}
        if len(ids) != len(self.steps):
            raise ValueError("步骤标识不能重复")
        if any(not set(step.depends_on) <= ids or step.id in step.depends_on for step in self.steps):
            raise ValueError("步骤依赖不存在或引用自身")
        self.layers()
        return self

    def layers(self) -> list[list[PlanStep]]:
        remaining = list(self.steps)
        done: set[str] = set()
        layers = []
        while remaining:
            ready = [step for step in remaining if set(step.depends_on) <= done]
            if not ready:
                raise ValueError("计划不能包含循环依赖")
            layers.append(ready)
            done.update(step.id for step in ready)
            remaining = [step for step in remaining if step.id not in done]
        return layers


class StepOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["succeeded", "failed", "blocked", "unknown"]
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    message: str = ""
    retryable: bool = False
    source: str = "offline"
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


@dataclass(frozen=True)
class Lease:
    task_id: UUID
    token: UUID
    generation: int
    spec_revision: int
    owner_email: str


@dataclass
class LayerToken:
    layer: int


@dataclass
class StepSignal:
    step_id: str
    status: str


@dataclass
class RunSignal:
    status: str
    layer: int


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def plan_hash(plan: Plan, spec: ProcurementSpec, *, contract_version: str = "procurement-v1") -> str:
    # MAF图指纹不含指令/参数。业务hash单独绑定这些内容和已确认需求。
    return canonical_hash(
        {
            "plan": plan.model_dump(mode="json"),
            "spec": spec.model_dump(mode="json"),
            "contract_version": contract_version,
        }
    )
