"""需求、计划与证据投影契约；不调用模型。"""

from uuid import uuid4

import pytest

from shared.procurement.dispatch import reuse_key, step_inputs
from shared.procurement.models import Plan, ProcurementSpec, StepOutcome
from shared.procurement.parsing import parse_offline
from shared.procurement.planning import fixed_plan, validate_plan


def task(**changes):
    spec = ProcurementSpec(query="耳机", quantity=3, total_budget="1500.00")
    return {
        "id": str(uuid4()),
        "owner_email": "buyer@example.test",
        "execution_mode": "offline",
        "requirements": {**spec.model_dump(mode="json"), **changes},
    }


def test_offline_parse_keeps_purchase_quantity_separate_from_option_count():
    proposal = parse_offline("给我两个独立备选方案，采购3副办公耳机，货款预算1500元，不含运费税费")
    assert proposal.draft_spec["quantity"] == 3
    assert proposal.draft_spec["alternative_count"] == 2
    assert proposal.draft_spec["total_budget"] == "1500.00"
    assert not proposal.questions
    assert proposal.source == "rule_based_offline"


@pytest.mark.parametrize("goal", ["帮我选耳机", "预算500元买耳机", "买3副耳机", "买0副耳机预算500元"])
def test_missing_critical_fields_are_not_silently_filled(goal):
    assert parse_offline(goal).questions


@pytest.mark.parametrize("goal", ["买3副耳机预算500美元", "买3副耳机预算500元明天送到", "买3副耳机预算500元含运费"])
def test_unverifiable_hard_constraints_require_revision(goal):
    assert parse_offline(goal).unsupported_conditions


def test_fixed_plan_has_seven_nodes_and_is_not_marked_as_model():
    plan = fixed_plan(task())
    assert len(plan.steps) == 7
    assert [len(layer) for layer in plan.layers()] == [1, 6]
    assert plan.source == "deterministic_demo"


@pytest.mark.parametrize("change", ["missing_stock", "override_quantity", "foreign_service", "wrong_slot"])
def test_plan_cannot_bypass_required_business_evidence(change):
    specimen = task()
    data = fixed_plan(specimen).model_dump(mode="json")
    if change == "missing_stock":
        data["steps"] = [row for row in data["steps"] if row["capability"] != "check_inventory"]
    elif change == "override_quantity":
        data["steps"][1]["parameters"]["quantity"] = 1
    elif change == "foreign_service":
        data["steps"][1]["capability"] = "create_order"
    else:
        data["steps"][1]["parameters"]["slot"] = 8
    with pytest.raises(ValueError):
        validate_plan(Plan.model_validate(data), ProcurementSpec.model_validate(specimen["requirements"]))


def test_reuse_tracks_actual_sku_instead_of_slot_or_parent_receipt():
    current = task()
    plan = fixed_plan(current)
    a, b, c = [str(uuid4()) for _ in range(3)]

    def deps(ids):
        return {
            "discover": StepOutcome(
                status="succeeded",
                data={"capability": "search_candidates", "items": [{"product_id": value} for value in ids]},
            )
        }

    old = reuse_key(current, plan.steps[4], deps([a, b]))
    revised = task(total_budget="1200.00")
    new = reuse_key(revised, plan.steps[1], deps([b, c]))
    assert old == new
    assert step_inputs(revised, plan.steps[1], deps([b, c])) == {"product_ids": [b]}
    quantity_changed = task(quantity=4)
    assert reuse_key(quantity_changed, plan.steps[1], deps([b, c])) != new


def test_repair_excludes_only_proven_infeasible_candidates():
    bad = str(uuid4())
    failures = [{"code": "INSUFFICIENT_STOCK", "affected_product_ids": [bad]}]
    plan = fixed_plan(task(), failures)
    assert plan.steps[0].parameters["exclude_ids"] == [bad]
    unchanged = fixed_plan(task(), [{"code": "PRICE_CHANGED", "affected_product_ids": [bad]}])
    assert unchanged.steps[0].parameters["exclude_ids"] == []


@pytest.mark.parametrize("condition", ["必须索尼品牌", "不得二手", "只要黑色", "续航至少20小时"])
def test_explicit_unmapped_hard_constraints_are_not_dropped(condition):
    assert parse_offline("采购3副耳机，预算1500元，" + condition).unsupported_conditions


def test_supported_budget_rating_and_stock_conditions_do_not_block_draft():
    parsed = parse_offline("采购3副耳机，预算不能超过1500元，评分至少4.5，必须现货，需要两个独立备选方案")
    assert not parsed.unsupported_conditions
    assert parsed.draft_spec["total_budget"] == "1500.00"
    assert parsed.draft_spec["min_rating"] == 4.5
