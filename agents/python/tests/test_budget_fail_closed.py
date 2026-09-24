"""余额／认证错误停止后续请求，错误体中的凭据不会进入上层日志。"""

from decimal import Decimal

import httpx
import pytest

from shared.campaign_budget import BudgetError, CampaignBudget, Price
from shared.paid_transport import PaidTransport


async def test_auth_error_halts_provider_and_redacts_error_body(tmp_path):
    budget = CampaignBudget(tmp_path / "budget.json")
    calls = []
    credential = "test-secret-do-not-log"

    def reject(request):
        calls.append(True)
        return httpx.Response(401, json={"error": "rejected " + credential})

    transport = PaidTransport(
        "deepseek", budget=budget, price=Price("CNY", Decimal(2), Decimal(8), "test"), inner=httpx.MockTransport(reject)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + credential},
            json={"messages": []},
        )
        assert credential not in response.text
        with pytest.raises(BudgetError):
            await client.post("https://api.deepseek.com/v1/chat/completions", json={"messages": []})
    assert calls == [True]
    assert budget.snapshot()["deepseek"]["unknown"] == 1


async def test_price_expiry_is_checked_again_when_request_is_sent(tmp_path):
    calls = []
    price = Price("CNY", Decimal(2), Decimal(8), "expired", "2000-01-01")
    transport = PaidTransport(
        "deepseek",
        budget=CampaignBudget(tmp_path / "budget.json"),
        price=price,
        inner=httpx.MockTransport(lambda request: calls.append(request)),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(BudgetError):
            await client.post("https://api.deepseek.com/v1/chat/completions", json={})
    assert calls == []
