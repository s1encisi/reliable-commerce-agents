"""采购跨专业服务执行、输入投影与最终事实核验。"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import httpx

from shared.config import settings
from shared.db import get_pool
from shared.factory import parse_agent_registry
from shared.oauth.service_client import build_a2a_headers
from shared.procurement.models import PlanStep, ProcurementSpec, StepOutcome, current_procurement_binding
from shared.procurement.planning import CAPABILITY_SERVICES


def as_data(outcome) -> dict:
    if isinstance(outcome, StepOutcome):
        return outcome.data
    return outcome.get("data", outcome)


def step_inputs(task: dict, step: PlanStep, dependencies: dict) -> dict:
    if step.capability == "search_candidates":
        return {
            "exclude_ids": step.parameters.get("exclude_ids", []),
            "candidate_limit": step.parameters.get("candidate_limit", 6),
        }
    rows = []
    for value in dependencies.values():
        data = as_data(value)
        if data.get("capability") == "search_candidates":
            rows.extend(data.get("items", []))
    slot = step.parameters["slot"]
    if slot >= len(rows):
        from shared.procurement.domain import DomainError

        raise DomainError("CANDIDATE_POOL_EXHAUSTED", "当前候选范围不足以形成全部备选方案")
    return {"product_ids": [str(rows[slot]["product_id"])]}


def reuse_key(task: dict, step: PlanStep, dependencies: dict) -> str:
    spec = ProcurementSpec.model_validate(task["requirements"]).model_dump(mode="json")
    inputs = step_inputs(task, step, dependencies)
    if step.capability == "search_candidates":
        fields = {key: spec.get(key) for key in ("query", "min_rating")}
    elif step.capability == "quote_options":
        fields = {key: spec.get(key) for key in ("quantity", "currency", "quote_policy")}
        fields["owner"] = task["owner_email"]
    elif step.capability == "check_inventory":
        fields = {"quantity": spec["quantity"]}
    else:
        fields = {}
    content = {"version": 1, "capability": step.capability, "inputs": inputs, "requirements": fields}
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def outcome_from_data(task: dict, capability: str, data: dict, *, source: str) -> StepOutcome:
    expected_source = f"tool:{capability}"
    if (
        data.get("source") != expected_source
        or data.get("capability") != capability
        or not isinstance(data.get("items"), list)
    ):
        return StepOutcome(
            status="failed", error_code="INVALID_EVIDENCE", message="专业服务没有返回匹配的工具回执", source=source
        )
    spec = ProcurementSpec.model_validate(task["requirements"])
    code = None
    message = ""
    affected = []
    for row in data["items"]:
        reason = None
        if capability == "quote_options" and Decimal(str(row["final_total"])) > spec.total_budget:
            reason = ("BUDGET_EXCEEDED", "候选方案超过确认后的货款预算")
        elif capability == "check_inventory" and int(row["available_quantity"]) < spec.quantity:
            reason = ("INSUFFICIENT_STOCK", "候选库存不足以购买确认数量")
        elif capability == "review_evidence" and not row.get("reviews"):
            reason = ("INSUFFICIENT_REVIEWS", "候选没有可供核实的评论证据")
        if reason:
            code, message = reason
            affected.append(row["product_id"])
    if affected:
        data = {**data, "affected_product_ids": affected, "error_code": code}
    return StepOutcome(
        status="failed" if code else "succeeded",
        data=data,
        error_code=code,
        message=message,
        source=source,
        artifacts=[data],
    )


async def dispatch_step(task: dict, step: PlanStep, dependencies: dict[str, StepOutcome]) -> StepOutcome:
    from shared.procurement.domain import DomainError

    try:
        inputs = step_inputs(task, step, dependencies)
    except DomainError as exc:
        return StepOutcome(status="failed", error_code=exc.code, message=str(exc), source="runtime")
    registry = parse_agent_registry(settings.AGENT_REGISTRY)
    service = CAPABILITY_SERVICES[step.capability]
    if service not in registry:
        return StepOutcome(status="blocked", error_code="SERVICE_UNAVAILABLE", message=f"未配置专业服务：{service}")
    headers = await build_a2a_headers()
    headers.update({"X-Execution-Policy": "read_only", "X-Root-Run-Id": str(task["id"])})
    from shared.paid_transport import current_run_deadline

    deadline = current_run_deadline.get()
    if deadline is not None:
        headers["X-Run-Deadline"] = str(deadline)
    body = {
        "task_id": str(task["id"]),
        "plan_revision": task["plan_revision"],
        "step_id": step.id,
        "capability": step.capability,
        "inputs": inputs,
        "binding": json.loads(json.dumps(current_procurement_binding.get() or {}, default=str)),
    }
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False) as client:
            response = await client.post(f"{registry[service]}/procurement:execute", headers=headers, json=body)
        if response.status_code == 429:
            return StepOutcome(status="blocked", error_code="BUDGET_EXHAUSTED", message="采购任务模型预算不足")
        if response.status_code == 409:
            return StepOutcome(status="blocked", error_code="STALE_PLAN", message="需求或执行计划已经更新")
        if response.status_code in {401, 403}:
            return StepOutcome(status="blocked", error_code="POLICY_REJECTED", message="专业服务拒绝身份或执行权限")
        if response.status_code == 422:
            detail = response.json().get("detail")
            if isinstance(detail, dict) and detail.get("code") in {
                "UNKNOWN_PRODUCT",
                "INVALID_QUANTITY",
                "INVALID_SPEC",
                "INVALID_INPUT",
                "UNKNOWN_OWNER",
                "INSUFFICIENT_STOCK",
                "PRICE_CHANGED",
                "COUPON_CHANGED",
                "INSUFFICIENT_REVIEWS",
            }:
                affected = [
                    value for value in detail.get("affected_product_ids", []) if value in inputs.get("product_ids", [])
                ]
                return StepOutcome(
                    status="failed",
                    error_code=detail["code"],
                    message=str(detail.get("message", "专业取证失败"))[:500],
                    data={"affected_product_ids": affected},
                    source="specialist_error",
                )
        response.raise_for_status()
        payload = response.json()
        return outcome_from_data(task, step.capability, payload["data"], source=payload["source"])
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        return StepOutcome(
            status="unknown",
            error_code="TRANSIENT_SERVICE_ERROR",
            message=f"专业服务结果未知：{type(exc).__name__}",
            retryable=True,
        )
    except (httpx.HTTPStatusError, ValueError, KeyError, TypeError) as exc:
        return StepOutcome(
            status="failed", error_code="SPECIALIST_ERROR", message=f"专业服务未完成：{type(exc).__name__}"
        )


async def finalize_task(task: dict, outcomes: dict[str, StepOutcome]) -> dict:
    from shared.procurement.domain import verify_options

    spec = ProcurementSpec.model_validate(task["requirements"]).model_dump(mode="json")
    evidence = [as_data(value) for value in outcomes.values()]
    found = {}
    names = {}
    for data in evidence:
        capability = data.get("capability")
        if capability == "search_candidates":
            names.update({row["product_id"]: row["name"] for row in data.get("items", [])})
            continue
        field = {"quote_options": "quote", "check_inventory": "inventory", "review_evidence": "review"}.get(capability)
        if not field:
            continue
        for row in data.get("items", []):
            found.setdefault(row["product_id"], {})[field] = row
    options = [
        {"product_id": pid, "name": names.get(pid, ""), "quantity": spec["quantity"], **parts}
        for pid, parts in found.items()
        if {"quote", "inventory", "review"} <= set(parts)
    ]
    report = await verify_options(get_pool(), spec, options, evidence, task["owner_email"])
    if not report["passed"]:
        return {
            "status": "needs_replan",
            "options": [],
            "failures": report["failures"],
            "verification": report,
            "stop_reason": "最终事实核验未通过，需要局部修复或用户处理",
        }
    verified = sorted(
        report["options"], key=lambda value: (Decimal(value["quote"]["final_total"]), value["product_id"])
    )
    return {
        "status": "completed",
        "options": verified,
        "verification": report,
        "stop_reason": "可验证的货款、数量和证据已核实；未下单、未锁库存。",
    }
