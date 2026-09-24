"""从最终回答提取可核验声明。

卡片声明来自 product、products、order 围栏块，属于界面交互数据，
enforce 模式可移除或修正。正文声明包含 UUID、$NNN.NN 金额和 TRK
物流号；先移除卡片块再提取，避免重复计数。

非法 JSON 卡片会被跳过，不使核验过程崩溃；其渲染由 rich-message.tsx
处理。现有金额格式按实现保留，不能仅修改说明就声称支持人民币解析。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_CARD_FENCE_RE = re.compile(r"```(product|products|order)\s*\n(.*?)\n?```", re.DOTALL)
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_AMOUNT_RE = re.compile(r"\$\s?(\d{1,6}(?:\.\d{2})?)")
_TRACKING_RE = re.compile(r"\bTRK[A-Z0-9]+\b")


@dataclass(frozen=True)
class ProductClaim:
    id: str
    name: str | None
    price: float | None
    image_url: str | None


@dataclass(frozen=True)
class OrderClaim:
    id: str
    status: str | None
    total: float | None
    tracking: str | None


@dataclass(frozen=True)
class BareIdClaim:
    id: str


@dataclass(frozen=True)
class AmountClaim:
    value: float


@dataclass(frozen=True)
class TrackingClaim:
    value: str


@dataclass
class ExtractedClaims:
    products: list[ProductClaim] = field(default_factory=list)
    orders: list[OrderClaim] = field(default_factory=list)
    bare_ids: list[BareIdClaim] = field(default_factory=list)
    amounts: list[AmountClaim] = field(default_factory=list)
    trackings: list[TrackingClaim] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return len(self.products) + len(self.orders) + len(self.bare_ids) + len(self.amounts) + len(self.trackings)


def extract_claims(text: str) -> ExtractedClaims:
    claims = ExtractedClaims()
    prose = text

    for match in _CARD_FENCE_RE.finditer(text):
        kind, raw_json = match.group(1), match.group(2)
        prose = prose.replace(match.group(0), "", 1)
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            continue

        if kind in ("product", "products"):
            entries = payload if isinstance(payload, list) else [payload]
            for entry in entries:
                claim = _product_claim(entry)
                if claim is not None:
                    claims.products.append(claim)
        elif kind == "order":
            claim = _order_claim(payload)
            if claim is not None:
                claims.orders.append(claim)

    card_ids = {c.id for c in claims.products} | {c.id for c in claims.orders}
    card_trackings = {c.tracking for c in claims.orders if c.tracking}

    for uuid_match in _UUID_RE.finditer(prose):
        if uuid_match.group(0) not in card_ids:
            claims.bare_ids.append(BareIdClaim(id=uuid_match.group(0)))

    for amount_match in _AMOUNT_RE.finditer(prose):
        claims.amounts.append(AmountClaim(value=float(amount_match.group(1))))

    for tracking_match in _TRACKING_RE.finditer(prose):
        if tracking_match.group(0) not in card_trackings:
            claims.trackings.append(TrackingClaim(value=tracking_match.group(0)))

    return claims


def _product_claim(entry: Any) -> ProductClaim | None:
    if not isinstance(entry, dict) or not entry.get("id"):
        return None
    return ProductClaim(
        id=str(entry["id"]),
        name=entry.get("name"),
        price=_as_float(entry.get("price")),
        image_url=entry.get("image_url"),
    )


def _order_claim(entry: Any) -> OrderClaim | None:
    if not isinstance(entry, dict) or not entry.get("id"):
        return None
    return OrderClaim(
        id=str(entry["id"]),
        status=entry.get("status"),
        total=_as_float(entry.get("total")),
        tracking=entry.get("tracking"),
    )


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def rewrite_cards(
    text: str,
    decide_product: Callable[[dict[str, Any]], dict[str, Any] | None],
    decide_order: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> str:
    """原地改写 product、products、order 围栏块。

    回调接收解析后的字典，返回保留或修正后的字典，返回 None 则移除。
    products 逐项过滤，全部移除时删除整个围栏。非法 JSON 保持原样。
    完全未改动的块按原字节返回，避免无谓 JSON 往返序列化改变格式。
    """

    def _replace(match: re.Match[str]) -> str:
        kind, raw_json = match.group(1), match.group(2)
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return match.group(0)

        if kind == "product":
            if not isinstance(payload, dict):
                return match.group(0)
            decided = decide_product(payload)
            if decided == payload:
                return match.group(0)
            return f"```product\n{json.dumps(decided, separators=(',', ':'))}\n```" if decided else ""

        if kind == "products":
            if not isinstance(payload, list):
                return match.group(0)
            kept = [decide_product(e) for e in payload if isinstance(e, dict)]
            kept = [e for e in kept if e is not None]
            if kept == payload:
                return match.group(0)
            return f"```products\n{json.dumps(kept, separators=(',', ':'))}\n```" if kept else ""

        if kind == "order":
            if not isinstance(payload, dict):
                return match.group(0)
            decided = decide_order(payload)
            if decided == payload:
                return match.group(0)
            return f"```order\n{json.dumps(decided, separators=(',', ':'))}\n```" if decided else ""

        return match.group(0)

    return _CARD_FENCE_RE.sub(_replace, text)
