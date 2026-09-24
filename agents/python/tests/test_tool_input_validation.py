"""敏感工具输入模型测试。

即使人工批准，非法参数也必须返回结构化错误，不能进入数据库更新。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.tool_inputs import (
    CancelOrderInput,
    InitiateReturnInput,
    ModifyOrderInput,
    ProcessRefundInput,
    ShippingAddress,
    validation_error_payload,
)

_GOOD_UUID = str(uuid4())
_GOOD_ADDR = {
    "street": "123 Market St",
    "city": "San Francisco",
    "state": "CA",
    "zip": "94105",
    "country": "US",
}


# ─────────────────────── CancelOrderInput ───────────────────────


def test_cancel_order_accepts_valid_uuid_and_reason() -> None:
    parsed = CancelOrderInput(order_id=_GOOD_UUID, reason="changed my mind")
    assert str(parsed.order_id) == _GOOD_UUID


@pytest.mark.parametrize(
    "order_id,reason",
    [
        ("not-a-uuid", "ok"),
        (_GOOD_UUID, ""),
        (_GOOD_UUID, " " * 600),
    ],
)
def test_cancel_order_rejects_garbage(order_id: str, reason: str) -> None:
    with pytest.raises(ValidationError):
        CancelOrderInput(order_id=order_id, reason=reason)


# ─────────────────────── ModifyOrderInput ───────────────────────


def test_modify_order_accepts_clean_address() -> None:
    parsed = ModifyOrderInput(order_id=_GOOD_UUID, new_address=_GOOD_ADDR)
    assert parsed.new_address.zip == "94105"
    assert parsed.new_address.state == "CA"


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("zip", "AAAA"),  # 非数字占位值。
        ("zip", ""),
        ("state", "California"),  # 完整名称，不是代码。
        ("country", "United States"),
        ("street", ""),
    ],
)
def test_modify_order_rejects_bad_address_fields(field: str, bad_value: str) -> None:
    addr = {**_GOOD_ADDR, field: bad_value}
    with pytest.raises(ValidationError):
        ModifyOrderInput(order_id=_GOOD_UUID, new_address=addr)


def test_modify_order_drops_unknown_address_keys() -> None:
    """extra=forbid 必须拒绝未知键，不能静默丢弃。"""
    bad_addr = {**_GOOD_ADDR, "<script>": "alert(1)"}
    with pytest.raises(ValidationError):
        ModifyOrderInput(order_id=_GOOD_UUID, new_address=bad_addr)


# ─────────────────────── InitiateReturnInput ───────────────────────


def test_initiate_return_defaults_refund_method_to_original_payment() -> None:
    parsed = InitiateReturnInput(order_id=_GOOD_UUID, reason="defective")
    assert parsed.refund_method == "original_payment"


def test_initiate_return_rejects_unknown_refund_method() -> None:
    with pytest.raises(ValidationError):
        InitiateReturnInput(order_id=_GOOD_UUID, reason="defective", refund_method="bitcoin")


# ─────────────────────── ProcessRefundInput ───────────────────────


def test_process_refund_rejects_non_uuid() -> None:
    with pytest.raises(ValidationError):
        ProcessRefundInput(return_id="not-a-uuid")


# ─────────────────────── error payload shape ───────────────────────


def test_validation_error_payload_lists_all_field_errors() -> None:
    try:
        ModifyOrderInput(order_id="bad", new_address={"street": ""})
    except ValidationError as exc:
        payload = validation_error_payload("modify_order", exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValidationError")

    assert payload["error"] == "Invalid input to modify_order"
    assert isinstance(payload["field_errors"], list)
    assert payload["field_errors"]  # 至少包含一项错误。
    fields = {fe["field"] for fe in payload["field_errors"]}
    assert "order_id" in fields


# ─────────────────────── ShippingAddress unicode handling ───────


def test_shipping_address_strips_whitespace_and_uppercases_codes() -> None:
    parsed = ShippingAddress(
        street="  221B Baker St  ",
        city="London",
        state="ld",
        zip="NW1 6XE",
        country="gb",
    )
    assert parsed.street == "221B Baker St"
    assert parsed.state == "LD"
    assert parsed.country == "GB"
