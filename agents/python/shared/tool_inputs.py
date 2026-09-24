"""敏感工具的 Pydantic 输入校验模型。

在数据库访问前严格检查 UUID、地址等结构，避免把模型提供的无效
字符串直接送入 UPDATE。校验失败返回结构化错误，供模型请求澄清。
人工审批不能替代参数校验，即使批准了畸形请求也必须拒绝执行。
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, field_validator

# 现有邮编规则支持美国 5 位或 9 位格式，
# 也接受部分英国、加拿大格式；本轮只翻译说明，
# 现有规则要求至少含一个数字，以拒绝 AAAA 一类输入。
# 这不是完整的国际地址验证，
# 不能据此推断所有国家邮编的真实规则。
_ZIP_PATTERN = re.compile(r"^(?=.*\d)[A-Za-z0-9 \-]{3,12}$")
_STATE_PATTERN = re.compile(r"^[A-Za-z]{2,3}$")
_COUNTRY_PATTERN = re.compile(r"^[A-Za-z]{2,3}$")
_REASON_MAX = 500


class ShippingAddress(BaseModel):
    """配送和账单地址校验。

    兼容当前支持的地址形态，同时拒绝明显非法邮编或过长文本；
    不代表覆盖所有国际地址格式。
    """

    street: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=2, max_length=3)
    zip: str = Field(min_length=3, max_length=12)
    country: str = Field(min_length=2, max_length=3)

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    @field_validator("zip")
    @classmethod
    def _zip_format(cls, v: str) -> str:
        if not _ZIP_PATTERN.match(v):
            raise ValueError("zip must be 3-12 alphanumerics, dashes or spaces")
        return v

    @field_validator("state")
    @classmethod
    def _state_format(cls, v: str) -> str:
        if not _STATE_PATTERN.match(v):
            raise ValueError("state must be a 2- or 3-letter code")
        return v.upper()

    @field_validator("country")
    @classmethod
    def _country_format(cls, v: str) -> str:
        if not _COUNTRY_PATTERN.match(v):
            raise ValueError("country must be a 2- or 3-letter ISO code")
        return v.upper()


class CancelOrderInput(BaseModel):
    order_id: UUID
    reason: str = Field(min_length=1, max_length=_REASON_MAX)

    model_config = {"extra": "forbid", "str_strip_whitespace": True}


class ModifyOrderInput(BaseModel):
    order_id: UUID
    new_address: ShippingAddress

    model_config = {"extra": "forbid"}


class InitiateReturnInput(BaseModel):
    order_id: UUID
    # 所有退货入口都限制在 returns.reason 的 VARCHAR(255) 范围内。
    reason: str = Field(min_length=1, max_length=255)
    refund_method: str = Field(default="original_payment")

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    @field_validator("refund_method")
    @classmethod
    def _refund_method_known(cls, v: str) -> str:
        if v not in {"original_payment", "store_credit"}:
            raise ValueError("refund_method must be 'original_payment' or 'store_credit'")
        return v


class ProcessRefundInput(BaseModel):
    return_id: UUID

    model_config = {"extra": "forbid"}


# ─────────────────────── Helper ───────────────────────


def clamp_limit(
    limit: Any,
    *,
    default: int = 10,
    maximum: int = 100,
) -> int:
    """将模型控制的 LIMIT 参数归一为安全整数。

    非整数或小于等于零时使用 default，超过 maximum 时截断。
    返回普通 int，避免模型字符串或过大数值直接进入 SQL 格式化。
    """
    try:
        value = int(limit) if limit is not None else default
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return min(value, maximum)


def validation_error_payload(name: str, exc: ValidationError) -> dict[str, Any]:
    """将 Pydantic 校验异常转为工具友好的错误字典。

    统一返回 error 与 field_errors，让模型或审批人定位字段问题，
    不暴露原始异常堆栈。
    """
    return {
        "error": f"Invalid input to {name}",
        "field_errors": [
            {
                "field": ".".join(str(p) for p in err.get("loc", ())),
                "message": err.get("msg", ""),
            }
            for err in exc.errors()
        ],
    }
