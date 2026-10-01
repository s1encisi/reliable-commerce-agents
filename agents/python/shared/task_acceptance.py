"""只读任务的受控验收：绑定步骤实体，重新读取授权事实，不信任助手完成声明。"""

from __future__ import annotations

import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shared.context import current_user_email

DEMO_TASK_GOAL = "筛选两款商品并核对库存"


class TaskRequirements(BaseModel):
    """目录原始货币单位；自然语言备注不是已核验的结构化约束。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    product_ids: list[UUID] = Field(default_factory=list, max_length=20)
    max_price: float | None = Field(default=None, ge=0)
    min_rating: float | None = Field(default=None, ge=0, le=5)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    min_items: int = Field(default=1, ge=1, le=20)
    in_stock: bool | None = None
    compare: bool = False


class StepAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["product_selection", "product_comparison", "inventory_check", "order_lookup"]
    required_ids: list[UUID] = Field(default_factory=list, max_length=20)
    min_items: int = Field(default=1, ge=1, le=20)
    from_steps: list[str] = Field(default_factory=list, max_length=8)


TOOLS = {
    "product_selection": {"search_products", "semantic_search", "get_product_details", "compare_products"},
    # 多次详情查询是 compare_products 的合法替代；普通检索不能冒充完整对比。
    "product_comparison": {"get_product_details", "compare_products"},
    "inventory_check": {"check_stock", "get_warehouse_availability"},
    "order_lookup": {"get_order_details", "get_user_orders", "get_tracking_status"},
}
SERVICES = {
    "product_selection": {"product-discovery"},
    "product_comparison": {"product-discovery"},
    "inventory_check": {"inventory-fulfillment", "product-discovery"},
    "order_lookup": {"order-management", "inventory-fulfillment"},
}


def evidence_ids(result: dict, acceptance: StepAcceptance) -> set[UUID]:
    """只消费服务端工具溯源，正文里的 ID、grounding 计数均不能增加证据。"""
    if result.get("requires_original_route"):
        return set()
    found = set()
    for step in result.get("steps") or []:
        tool = step.get("tool_name")
        provenance = step.get("provenance") or {}
        if (
            tool not in TOOLS[acceptance.kind]
            or step.get("status") != "success"
            or step.get("result_confirmed") is not True
            or provenance.get("source") != f"tool:{tool}"
        ):
            continue
        for value in provenance.get("row_ids", []):
            try:
                found.add(UUID(str(value)))
            except (ValueError, TypeError):
                continue
    return found


async def verify_step(conn, task: dict, step, result: dict) -> dict:
    acceptance = step.acceptance
    report = {
        "passed": False,
        "errors": [],
        "evidence": [],
        "validation_scope": [],
        "unverified_constraints": task["constraints"],
    }
    if acceptance is None:
        report["errors"].append("MISSING_ACCEPTANCE_CONTRACT")
        return report
    requirements = TaskRequirements.model_validate(task.get("requirements") or {})
    ids = evidence_ids(result, acceptance)
    for dependency in step.depends_on:
        if task["results"].get(dependency, {}).get("valid_until", 0) <= time.time():
            report["errors"].append("DEPENDENCY_EXPIRED")
    required = set(acceptance.required_ids)
    if acceptance.kind != "order_lookup":
        required.update(requirements.product_ids)
    for dependency in acceptance.from_steps:
        receipt = task["results"].get(dependency, {})
        verification = receipt.get("verification", {})
        if not verification.get("passed"):
            report["errors"].append("DEPENDENCY_NOT_VERIFIED")
        required.update(UUID(row["id"]) for row in verification.get("evidence", []))
    if not ids:
        report["errors"].append("NO_RELEVANT_TOOL_EVIDENCE")
        return report
    # 对明确对象只核实指定集合；搜索到的额外候选不是最终推荐。
    selected = required if required else ids
    if not required <= ids:
        report["errors"].append("REQUIRED_ENTITIES_MISSING")
    minimum = acceptance.min_items
    if acceptance.kind != "order_lookup":
        minimum = max(minimum, requirements.min_items)
    if acceptance.kind == "product_comparison":
        minimum = max(minimum, 2)
    if len(selected) < minimum:
        report["errors"].append("INSUFFICIENT_ENTITIES")
    if acceptance.kind == "order_lookup":
        rows = await conn.fetch(
            "SELECT o.id,o.status,o.total FROM orders o JOIN users u ON u.id=o.user_id "
            "WHERE o.id=ANY($1::uuid[]) AND u.email=$2 ORDER BY o.id",
            list(selected),
            current_user_email.get(""),
        )
        report["validation_scope"] = ["order_identity", "current_owner", "current_status", "current_total"]
    else:
        rows = await conn.fetch(
            "SELECT p.id,p.name,p.category,p.price,p.rating,p.specs,"
            "COALESCE((SELECT SUM(wi.quantity) FROM warehouse_inventory wi WHERE wi.product_id=p.id),0) AS quantity "
            "FROM products p WHERE p.id=ANY($1::uuid[]) AND p.is_active=TRUE ORDER BY p.id",
            list(selected),
        )
        report["validation_scope"] = ["product_identity", "current_catalog_facts"]
        if acceptance.kind == "product_comparison":
            report["validation_scope"].append("comparison_objects_and_attributes")
        if acceptance.kind == "inventory_check":
            report["validation_scope"].append("current_inventory")
        for field in ("max_price", "min_rating", "category", "in_stock"):
            if getattr(requirements, field) is not None:
                report["validation_scope"].append(field)
    if len(rows) != len(selected):
        report["errors"].append("ENTITY_NOT_FOUND_OR_NOT_AUTHORIZED")
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        for field in ("price", "rating", "total"):
            if item.get(field) is not None:
                item[field] = float(item[field])
        if acceptance.kind != "order_lookup":
            if requirements.max_price is not None and item["price"] > requirements.max_price:
                report["errors"].append("PRICE_LIMIT_EXCEEDED")
            if requirements.min_rating is not None and (
                item["rating"] is None or item["rating"] < requirements.min_rating
            ):
                report["errors"].append("RATING_BELOW_MINIMUM")
            if requirements.category is not None and item["category"] != requirements.category:
                report["errors"].append("CATEGORY_MISMATCH")
            if requirements.in_stock is not None and (item["quantity"] > 0) != requirements.in_stock:
                report["errors"].append("STOCK_REQUIREMENT_NOT_MET")
        report["evidence"].append(item)
    report["errors"] = list(dict.fromkeys(report["errors"]))
    report["passed"] = not report["errors"]
    return report
