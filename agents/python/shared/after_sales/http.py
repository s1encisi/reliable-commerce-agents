"""由客户端生成的操作 ID 保持在模型可控的工具参数之外。"""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import HTTPException, Request

from shared.after_sales.operations import current_operation_id


async def operation_scope(request: Request) -> AsyncIterator[None]:
    value = request.headers.get("Idempotency-Key")
    if value:
        try:
            value = str(UUID(value))
        except ValueError as exc:
            raise HTTPException(400, "Idempotency-Key must be a UUID") from exc
    token = current_operation_id.set(value)
    try:
        yield
    finally:
        current_operation_id.reset(token)
