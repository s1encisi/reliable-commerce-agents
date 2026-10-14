"""受控采购计划与局部修复；数据选择来自真实候选回执。"""

from __future__ import annotations

from uuid import UUID

from shared.procurement.models import Plan, PlanStep, ProcurementSpec

CAPABILITY_SERVICES = {
    "search_candidates": "product-discovery",
    "quote_options": "pricing-promotions",
    "check_inventory": "inventory-fulfillment",
    "review_evidence": "review-sentiment",
}
LABELS = {
    "search_candidates": "检索采购候选",
    "quote_options": "核对单券货款",
    "check_inventory": "核对采购数量",
    "review_evidence": "读取评论证据",
}
REPLACE_CODES = {"INSUFFICIENT_STOCK", "BUDGET_EXCEEDED", "RATING_TOO_LOW", "UNKNOWN_PRODUCT", "INSUFFICIENT_REVIEWS"}


def validate_plan(plan: Plan, spec: ProcurementSpec) -> Plan:
    """结构合法之外校验能力覆盖与对象传递，不允许计划更改已确认硬条件。"""
    previous = {}
    searches = set()
    covered = {cap: set() for cap in CAPABILITY_SERVICES if cap != "search_candidates"}
    for step in plan.steps:
        if step.capability not in CAPABILITY_SERVICES:
            raise ValueError("计划包含未知采购能力")
        if step.id in previous or not set(step.depends_on) <= set(previous):
            raise ValueError("计划依赖必须按拓扑顺序且步骤标识唯一")
        if step.capability == "search_candidates":
            if set(step.parameters) - {"exclude_ids", "candidate_limit"}:
                raise ValueError("候选检索不能改写已确认的商品条件")
            limit = step.parameters.get("candidate_limit", 6)
            if type(limit) is not int or not spec.alternative_count <= limit <= 6:
                raise ValueError("候选数量上限非法")
            excluded = step.parameters.get("exclude_ids", [])
            if not isinstance(excluded, list) or len(excluded) > 12:
                raise ValueError("排除候选必须是有界UUID列表")
            for value in excluded:
                UUID(str(value))
            searches.add(step.id)
        else:
            if set(step.parameters) != {"slot"}:
                raise ValueError("专业节点必须绑定一个备选位置，不能注入任意商品或业务参数")
            slot = step.parameters["slot"]
            if type(slot) is not int or not 0 <= slot < spec.alternative_count:
                raise ValueError("备选位置与确认需求不符")
            if not set(step.depends_on) & searches:
                raise ValueError("专业节点必须直接依赖商品检索证据")
            if slot in covered[step.capability]:
                raise ValueError("同一备选不应重复规划同类取证")
            covered[step.capability].add(slot)
        previous[step.id] = step
    if len(searches) != 1:
        raise ValueError("一期采购计划必须有且仅有一项候选检索")
    if any(slots != set(range(spec.alternative_count)) for slots in covered.values()):
        raise ValueError("计划没有覆盖全部备选的报价、库存与评论验收")
    return plan


def _failed_products(failures: list[dict]) -> list[str]:
    ids = set()
    for failure in failures:
        data = failure.get("data") or {}
        code = failure.get("code") or failure.get("error_code") or data.get("error_code")
        if code in REPLACE_CODES:
            ids.update(failure.get("affected_product_ids") or data.get("affected_product_ids") or [])
            if failure.get("product_id"):
                ids.add(failure["product_id"])
            if not ids:
                ids.update(row["product_id"] for row in data.get("items", []) if "product_id" in row)
    return sorted(ids)


def fixed_plan(task: dict, failures: list[dict] | None = None) -> Plan:
    spec = ProcurementSpec.model_validate(task["requirements"])
    excluded = set(_failed_products(failures or []))
    prior = task.get("plan") or task.get("previous_plan") or {}
    if isinstance(prior, dict):
        for step in prior.get("steps", []):
            if step.get("capability") == "search_candidates":
                excluded.update(step.get("parameters", {}).get("exclude_ids", []))
    steps = [
        PlanStep(
            id="discover",
            capability="search_candidates",
            instruction=LABELS["search_candidates"],
            parameters={"candidate_limit": 6, "exclude_ids": sorted(excluded)},
        )
    ]
    for slot in range(spec.alternative_count):
        for capability, prefix in (
            ("quote_options", "quote"),
            ("check_inventory", "stock"),
            ("review_evidence", "reviews"),
        ):
            steps.append(
                PlanStep(
                    id=f"{prefix}-{slot + 1}",
                    capability=capability,
                    instruction=LABELS[capability],
                    parameters={"slot": slot},
                    depends_on=["discover"],
                )
            )
    return validate_plan(
        Plan(
            steps=steps,
            source="deterministic_demo",
            rationale="固定离线依赖计划；按真实失败排除不合格候选，其余有效证据可复用。",
        ),
        spec,
    )


def reusable_summary(task: dict) -> list[dict]:
    """规划器只需知道事实范围和引用，完整评论等保存在工具回执中。"""
    summaries = []
    for item in task.get("reusable_outcomes", [])[:16]:
        outcome = item.get("outcome") or {}
        data = outcome.get("data") or {}
        summaries.append(
            {
                "step_id": item.get("step_id"),
                "capability": data.get("capability"),
                "product_ids": [row.get("product_id") for row in data.get("items", [])],
                "valid_until": item.get("valid_until"),
                "input_hash": item.get("input_hash"),
                "status": outcome.get("status"),
            }
        )
    return summaries


async def generate_plan(task: dict, repair: bool = False, failures: list[dict] | None = None) -> Plan:
    if task["execution_mode"] == "offline":
        return fixed_plan(task, failures if repair else None)
    from shared.procurement.model_io import structured_response

    spec = ProcurementSpec.model_validate(task["requirements"])
    plan = await structured_response(
        task,
        "procurement-planner.yaml",
        {
            "confirmed_requirements": spec.model_dump(mode="json"),
            "repair": repair,
            "failures": failures or [],
            "previous_plan": task.get("plan") or task.get("previous_plan"),
            "reusable_outcomes": reusable_summary(task),
            "capabilities": CAPABILITY_SERVICES,
        },
        Plan,
    )
    plan.source = "model"
    validate_plan(plan, spec)
    # 候选替换必须覆盖实际不可行对象，不能继续挑同一个失败SKU。
    if repair:
        excluded = set(_failed_products(failures or []))
        actual = next(s for s in plan.steps if s.capability == "search_candidates")
        if not excluded <= set(actual.parameters.get("exclude_ids", [])):
            raise ValueError("修复计划没有排除已被证实不可行的候选")
    return plan
