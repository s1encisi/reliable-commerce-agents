"""费用预留跨实例、进程竞争、未知结果和真实 HTTP 边界测试。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest

from shared.campaign_budget import BudgetError, CampaignBudget, Price
from shared.paid_transport import PaidTransport


def test_reservations_survive_restart_and_do_not_oversubscribe(tmp_path):
    path = tmp_path / "budget.json"

    def reserve(_):
        try:
            return CampaignBudget(path).reserve(
                "jev", "jev-1.13.0", 300_000, currency="USD", purpose="probe", price_version="test"
            )
        except BudgetError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(12)))
    assert sum(x is not None for x in results) == 3
    assert CampaignBudget(path).snapshot()["jev"]["committed_or_reserved"] == 0.9
    first = next(x for x in results if x)
    CampaignBudget(path).settle(first, None)
    assert CampaignBudget(path).snapshot()["jev"]["committed_or_reserved"] == 0.9
    CampaignBudget(path).settle(first, 20_000, {"input_tokens": 100})
    assert CampaignBudget(path).snapshot()["jev"]["committed_or_reserved"] == 0.62


def test_corruption_and_actual_overrun_fail_closed(tmp_path):
    path = tmp_path / "budget.json"
    budget = CampaignBudget(path)
    attempt = budget.reserve("moonshot", "kimi-k3", 10, currency="CNY", purpose="planning", price_version="test")
    budget.settle(attempt, 20)
    with pytest.raises(BudgetError):
        budget.reserve("moonshot", "kimi-k3", 10, currency="CNY", purpose="planning", price_version="test")
    path.write_text("broken")
    with pytest.raises(BudgetError):
        budget.snapshot()


async def test_transport_counts_every_attempt_and_unknowns(tmp_path):
    budget = CampaignBudget(tmp_path / "budget.json")
    price = Price("CNY", Decimal("1"), Decimal("2"), "test")
    calls = []

    async def respond(request):
        import json

        body = json.loads(request.content)
        calls.append(body)
        assert body["thinking"] == {"type": "disabled"}
        if len(calls) == 1:
            return httpx.Response(200, json={"usage": {"prompt_tokens": 50, "completion_tokens": 10}})
        raise httpx.ReadTimeout("lost response")

    transport = PaidTransport("deepseek", budget=budget, price=price, inner=httpx.MockTransport(respond))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post("https://api.deepseek.com/chat/completions", json={"messages": [], "model": "x"})
        with pytest.raises(httpx.ReadTimeout):
            await client.post("https://api.deepseek.com/chat/completions", json={"messages": [], "model": "x"})
    result = budget.snapshot()["deepseek"]
    assert result["calls"] == 2 and result["unknown"] == 1
    assert result["committed_or_reserved"] > 0.00007


async def test_stream_cancellation_keeps_reservation(tmp_path):
    budget = CampaignBudget(tmp_path / "budget.json")

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices": []}\n\n'
            await asyncio.sleep(60)

    transport = PaidTransport(
        "deepseek",
        budget=budget,
        price=Price("CNY", Decimal("1"), Decimal("1"), "test"),
        inner=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream())),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        async with client.stream(
            "POST", "https://api.deepseek.com/chat/completions", json={"stream": True}
        ) as response:
            async for _ in response.aiter_bytes():
                break
    assert budget.snapshot()["deepseek"]["unknown"] == 1
