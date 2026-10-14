"""采购需求解析；离线规则只提出草案，始终由用户确认。"""

from __future__ import annotations

import re
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ParseProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_spec: dict = Field(default_factory=dict)
    questions: list[str] = Field(default_factory=list, max_length=12)
    unsupported_conditions: list[str] = Field(default_factory=list, max_length=12)
    source: str = "rule_based_offline"


def parse_offline(goal: str) -> ParseProposal:
    """有界规则解析，不假装完成开放语义理解，不静默改写货币和交付条件。"""
    small = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    count = re.search(r"([0-9]+|[一二两三四五六七八九十])\s*(?:副|件|台|套|个|本)(?!\s*(?:独立)?(?:备选)?方案)", goal)
    quantity = None
    if count:
        raw = count.group(1)
        quantity = int(raw) if raw.isdigit() else small[raw]
    budget = re.search(
        r"(?:总?预算|货款上限)\s*(?:不能超过|不超过|最多|为|是|[:：])?\s*[¥￥]?\s*([0-9]+(?:\.[0-9]{1,2})?)", goal
    )
    query = next(
        (
            name
            for name in (
                "采购专用耳机",
                "办公耳机",
                "降噪耳机",
                "耳机",
                "键盘",
                "鼠标",
                "显示器",
                "办公椅",
                "充电宝",
                "图书",
            )
            if name in goal
        ),
        "",
    )
    rating = re.search(r"评分\s*(?:至少|不低于|大于等于|>=|≥)?\s*([0-5](?:\.[0-9]+)?)", goal)
    alternatives = 1 if re.search(r"(?:一个|一套|1个|1套)\s*(?:备选)?方案", goal) else 2
    unsupported = []
    if re.search(r"美元|欧元|英镑|USD|EUR|GBP|\$|£|€", goal, re.I):
        unsupported.append("一期只支持人民币货款，不自动进行汇率转换。")
    if re.search(
        r"明天|后天|[0-9一二三四五六七]+天(?:内)?(?:到|送)|免运费|(?<!不)含税|(?<!不)含运费|(?<!不)叠加", goal
    ):
        unsupported.append("交期、运费税费和优惠叠加不属于一期可核实条件，请调整或保留为备注。")
    if re.search(r"必须.*(?:降噪|舒适|音质)|降噪必须|续航至少", goal):
        unsupported.append("性能和主观体验需要具体规格或人工判断，当前不能自动证明满足。")
    # 对明确的额外硬条件保持保守：库存/预算/评分等已支持字段之外，
    # 不能把品牌、颜色、成色或交期要求默默降成无约束检索。
    remaining = goal
    known = (
        r"(?:总?货款|总)?预算\s*(?:不能超过|不超过|最多|为|是|[:：])?\s*[¥￥]?\s*[0-9]+(?:\.[0-9]+)?\s*(?:元|人民币)?",
        r"评分\s*(?:至少|不低于|大于等于|>=|≥)?\s*[0-5](?:\.[0-9]+)?",
        r"(?:必须|只要|仅限|要求)?\s*(?:有现货|现货|有货|库存充足|库存足够)",
        r"(?:至少)?\s*(?:两个|两套|二个|一个|2个|1个)\s*(?:独立)?(?:备选)?方案",
        r"不(?:含运费税费|含运费|含税|叠加优惠)",
    )
    for pattern in known:
        remaining = re.sub(pattern, "", remaining)
    if re.search(r"必须|只要|仅限|不得|不要|不能|不超过|至少|不低于", remaining):
        unsupported.append("存在尚未映射的明确硬条件，请核对原始需求；一期仅自动核实商品范围、数量、预算、评分和现货。")
    questions = list(dict.fromkeys(unsupported))
    if not query:
        questions.append("请明确要采购的商品类型。")
    if quantity is None or not 1 <= quantity <= 100:
        questions.append("请填写 1 至 100 的采购数量。")
    if budget is None or Decimal(budget.group(1)) <= 0:
        questions.append("请填写大于零的人民币货款预算。")
    draft = {
        "query": query,
        "quantity": quantity,
        "alternative_count": alternatives,
        "total_budget": format(Decimal(budget.group(1)), ".2f") if budget else None,
        "currency": "CNY",
        "in_stock": True,
        "min_rating": float(rating.group(1)) if rating else None,
        "preferences": [goal] if unsupported else [],
        "budget_scope": "goods",
        "quote_policy": "single_coupon_v1",
    }
    return ParseProposal(draft_spec=draft, questions=questions, unsupported_conditions=unsupported)
