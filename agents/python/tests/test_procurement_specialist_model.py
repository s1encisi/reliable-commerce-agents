"""真实MAF工具循环消费预设模型响应；不访问任何模型网络。"""

from uuid import uuid4

import pytest
from agent_framework import ChatResponse, Content, Message
from fastapi import HTTPException

from shared.procurement.models import ProcurementSpec
from tests.test_orchestration_modes import _ScriptedClient, _text_response


def tool_call(arguments=None):
    return ChatResponse(
        messages=[
            Message(
                role="assistant",
                contents=[
                    Content.from_function_call(
                        call_id=str(uuid4()),
                        name="quote_options",
                        arguments=arguments or {},
                    )
                ],
            )
        ],
        response_id=str(uuid4()),
        finish_reason="tool_calls",
    )


@pytest.fixture
def specialist(monkeypatch):
    import shared.factory
    from shared.procurement import domain, model_io, service

    monkeypatch.setattr(model_io, "require_model_authorization", lambda: None)
    monkeypatch.setattr(service, "get_pool", lambda: object())
    calls = []
    data = {
        "capability": "quote_options",
        "source": "tool:quote_options",
        "items": [{"product_id": str(uuid4()), "quantity": 3, "final_total": "1470.00"}],
    }

    async def execute(pool, capability, spec, inputs, owner):
        calls.append((capability, spec, inputs, owner))
        return data

    monkeypatch.setattr(domain, "execute_capability", execute)
    task = {
        "id": str(uuid4()),
        "owner_email": "model-test@example.test",
        "execution_mode": "model",
        "spec_revision": 1,
        "requirements": ProcurementSpec(query="耳机", quantity=3, total_budget="1500.00").model_dump(mode="json"),
    }

    def use(*responses):
        client = _ScriptedClient(*responses)
        monkeypatch.setattr(shared.factory, "get_compatible_client", lambda provider: client)
        return client

    return service, task, data, calls, use


async def test_model_text_cannot_replace_actual_tool_evidence(specialist):
    service, task, data, calls, use = specialist
    client = use(tool_call(), _text_response("模型声称本方案只有1元。"))
    result = await service.execute_bound(task, "quote_options", {"product_ids": [data["items"][0]["product_id"]]})
    assert result["data"] == data
    assert result["data"]["items"][0]["final_total"] == "1470.00"
    assert calls[0][1]["quantity"] == 3
    assert result["source"] == "model_tool_execution"
    assert len(client.calls) == 1  # 成功取证后不再发出总结模型请求


async def test_json_shaped_answer_without_tool_call_is_rejected(specialist):
    service, task, data, calls, use = specialist
    import json

    use(_text_response(json.dumps(data)))
    with pytest.raises(HTTPException) as exc:
        await service.execute_bound(task, "quote_options", {"product_ids": [data["items"][0]["product_id"]]})
    assert exc.value.status_code == 422 and not calls


async def test_repeated_model_tool_call_returns_the_same_bound_receipt(specialist):
    service, task, data, calls, use = specialist
    batch = ChatResponse(
        messages=[
            Message(
                role="assistant",
                contents=[
                    Content.from_function_call(call_id="a", name="quote_options", arguments={}),
                    Content.from_function_call(call_id="b", name="quote_options", arguments={}),
                ],
            )
        ],
        response_id=str(uuid4()),
        finish_reason="tool_calls",
    )
    client = use(batch)
    await service.execute_bound(task, "quote_options", {"product_ids": [data["items"][0]["product_id"]]})
    assert len(calls) == 1
    assert len(client.calls) == 1


async def test_wrapped_sdk_budget_error_keeps_budget_semantics(specialist, monkeypatch):
    import shared.factory
    from shared.campaign_budget import BudgetError

    service, task, data, calls, _ = specialist

    class FailingClient(_ScriptedClient):
        async def _next(self):
            try:
                raise BudgetError("model budget exhausted")
            except BudgetError as exc:
                raise RuntimeError("wrapped SDK error") from exc

    monkeypatch.setattr(shared.factory, "get_compatible_client", lambda provider: FailingClient())
    with pytest.raises(BudgetError):
        await service.execute_bound(task, "quote_options", {"product_ids": [data["items"][0]["product_id"]]})
    assert calls == []


async def test_model_tool_business_failure_keeps_repair_scope(specialist, monkeypatch):
    from shared.procurement import domain

    service, task, data, _, use = specialist
    pid = data["items"][0]["product_id"]

    async def unavailable(*args, **kwargs):
        raise domain.DomainError("UNKNOWN_PRODUCT", "商品已下架", [pid])

    monkeypatch.setattr(domain, "execute_capability", unavailable)
    client = use(tool_call())
    with pytest.raises(domain.DomainError) as error:
        await service.execute_bound(task, "quote_options", {"product_ids": [pid]})
    assert error.value.code == "UNKNOWN_PRODUCT" and error.value.product_ids == [pid]
    assert len(client.calls) == 1
