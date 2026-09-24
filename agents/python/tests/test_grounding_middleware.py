"""事实核验模式分派测试。

台账层覆盖 off、observe、annotate；真实数据库层覆盖 enforce
对不存在或金额错误卡片的删除和修正。
"""

from __future__ import annotations

from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio
from agent_framework import AgentResponse, Content, Message, ResponseStream

import shared.db as shared_db
from shared.config import settings
from shared.grounding.ledger import GroundingLedger, ProductFact, current_grounding_ledger
from shared.grounding.middleware import GroundingVerificationMiddleware

_PRODUCT_ID = "0fd372fa-ecb2-4db0-bb71-8628a784ced9"
_MISSING_ID = "99999999-9999-9999-9999-999999999999"


def _agent_context(*, stream: bool, result) -> SimpleNamespace:
    return SimpleNamespace(stream=stream, result=result, stream_result_hooks=[])


def _text_response(text: str) -> AgentResponse:
    return AgentResponse(messages=[Message(role="assistant", contents=[Content.from_text(text=text)])])


@pytest.fixture(autouse=True)
def _reset_grounding_state(monkeypatch: pytest.MonkeyPatch):
    current_grounding_ledger.set(None)
    monkeypatch.setattr(shared_db, "_pool", None)
    yield


# ─────────────────────── Mode dispatch (no DB) ───────────────────────


@pytest.mark.asyncio
async def test_off_mode_skips_verification_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GROUNDING_MODE", "off")
    response = _text_response(f'```product\n{{"name": "X", "id": "{_PRODUCT_ID}", "price": 5.0}}\n```')
    ctx = _agent_context(stream=False, result=response)

    async def call_next() -> None:
        ctx.result = response

    await GroundingVerificationMiddleware().process(ctx, call_next)
    assert response.additional_properties == {}


@pytest.mark.asyncio
async def test_observe_mode_verifies_but_does_not_attach_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GROUNDING_MODE", "observe")
    ledger = GroundingLedger()
    ledger.products[_PRODUCT_ID] = ProductFact(id=_PRODUCT_ID, name="X", price=5.0)
    current_grounding_ledger.set(ledger)

    response = _text_response(f'```product\n{{"name": "X", "id": "{_PRODUCT_ID}", "price": 5.0}}\n```')
    ctx = _agent_context(stream=False, result=response)

    async def call_next() -> None:
        ctx.result = response

    mw = GroundingVerificationMiddleware()
    await mw.process(ctx, call_next)

    assert "grounding" not in response.additional_properties
    assert mw.verified_total == 1  # 内部仍计数，供可观测性使用。


@pytest.mark.asyncio
async def test_annotate_mode_attaches_report_without_editing_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GROUNDING_MODE", "annotate")
    ledger = GroundingLedger()
    ledger.products[_PRODUCT_ID] = ProductFact(id=_PRODUCT_ID, name="X", price=5.0)
    current_grounding_ledger.set(ledger)

    original_text = f'```product\n{{"name": "X", "id": "{_PRODUCT_ID}", "price": 5.0}}\n```'
    response = _text_response(original_text)
    ctx = _agent_context(stream=False, result=response)

    async def call_next() -> None:
        ctx.result = response

    await GroundingVerificationMiddleware().process(ctx, call_next)

    report = response.additional_properties["grounding"]
    assert report["verified"] == 1
    assert report["unverified"] == 0
    assert response.text == original_text


@pytest.mark.asyncio
async def test_no_claims_in_text_is_a_cheap_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GROUNDING_MODE", "annotate")
    response = _text_response("Sure, happy to help with that — no products involved here.")
    ctx = _agent_context(stream=False, result=response)

    async def call_next() -> None:
        ctx.result = response

    await GroundingVerificationMiddleware().process(ctx, call_next)
    assert response.additional_properties == {}


@pytest.mark.asyncio
async def test_enforce_mode_does_not_strip_when_unverifiable(monkeypatch: pytest.MonkeyPatch) -> None:
    # 台账未命中且无数据库时，保持原卡片，
    # 不能仅因核验无法执行，
    # 就删除可能真实的内容。
    monkeypatch.setattr(settings, "GROUNDING_MODE", "enforce")
    original_text = f'```product\n{{"name": "X", "id": "{_PRODUCT_ID}", "price": 5.0}}\n```'
    response = _text_response(original_text)
    ctx = _agent_context(stream=False, result=response)

    async def call_next() -> None:
        ctx.result = response

    await GroundingVerificationMiddleware().process(ctx, call_next)
    assert response.text == original_text
    assert response.additional_properties["grounding"]["claims"][0]["status"] == "unverifiable"


@pytest.mark.asyncio
async def test_streaming_registers_a_result_hook_instead_of_editing_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "GROUNDING_MODE", "annotate")

    async def _empty_updates():
        return
        yield  # pragma: no cover - unreachable; makes this an async generator

    stream = ResponseStream(_empty_updates(), finalizer=lambda updates: _text_response("hi"))
    ctx = _agent_context(stream=True, result=stream)

    async def call_next() -> None:
        ctx.result = stream

    await GroundingVerificationMiddleware().process(ctx, call_next)

    assert ctx.result is stream  # 不立即替换，等流结束后才修改最终响应。
    assert len(ctx.stream_result_hooks) == 1


@pytest.mark.asyncio
async def test_streaming_hook_fires_on_plain_iteration_to_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    # 宿主流式辅助函数依赖此机制：
    # 只进行 async for 迭代，
    # 也应触发已注册的最终结果钩子。
    monkeypatch.setattr(settings, "GROUNDING_MODE", "annotate")
    ledger = GroundingLedger()
    ledger.products[_PRODUCT_ID] = ProductFact(id=_PRODUCT_ID, name="X", price=5.0)
    current_grounding_ledger.set(ledger)

    card_text = f'```product\n{{"name": "X", "id": "{_PRODUCT_ID}", "price": 5.0}}\n```'

    async def _empty_updates():
        return
        yield  # pragma: no cover - unreachable; makes this an async generator

    stream = ResponseStream(_empty_updates(), finalizer=lambda updates: _text_response(card_text))
    ctx = _agent_context(stream=True, result=stream)

    async def call_next() -> None:
        ctx.result = stream

    await GroundingVerificationMiddleware().process(ctx, call_next)
    # 模拟 AgentMiddlewarePipeline 在每次执行后触发结果钩子。
    # 即中间件 process() 返回之后。
    for hook in ctx.stream_result_hooks:
        stream.with_result_hook(hook)

    async for _ in stream:
        pass  # 即使没有分块，仅流耗尽也必须触发钩子。

    final = await stream.get_final_response()
    assert final.additional_properties["grounding"]["verified"] == 1


# ─────────────────────── enforce mode, real strip/correct (DB-backed) ─────


@pytest_asyncio.fixture
async def db_pool(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


@pytest_asyncio.fixture
async def seeded_product(db_pool: asyncpg.Pool) -> dict:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO products (name, description, category, brand, price, is_active)
               VALUES ('Widget', 'A widget', 'Electronics', 'Acme', 49.99, TRUE)
               RETURNING id, name, price""",
        )
        return dict(row)


@pytest.mark.asyncio
async def test_enforce_mode_strips_a_fabricated_card(monkeypatch: pytest.MonkeyPatch, db_pool: asyncpg.Pool) -> None:
    monkeypatch.setattr(settings, "GROUNDING_MODE", "enforce")
    response = _text_response(
        f'Here you go:\n```product\n{{"name": "Ghost Widget", "id": "{_MISSING_ID}", "price": 5.0}}\n```\nEnjoy!'
    )
    ctx = _agent_context(stream=False, result=response)

    async def call_next() -> None:
        ctx.result = response

    await GroundingVerificationMiddleware().process(ctx, call_next)

    assert "```product" not in response.text
    assert _MISSING_ID not in response.text
    assert "Here you go:" in response.text and "Enjoy!" in response.text
    assert response.additional_properties["grounding"]["claims"][0]["status"] == "not_found"


@pytest.mark.asyncio
async def test_enforce_mode_corrects_a_wrong_price(
    monkeypatch: pytest.MonkeyPatch,
    db_pool: asyncpg.Pool,
    seeded_product: dict,
) -> None:
    monkeypatch.setattr(settings, "GROUNDING_MODE", "enforce")
    pid = str(seeded_product["id"])
    response = _text_response(f'```product\n{{"name": "Widget", "id": "{pid}", "price": 1.0}}\n```')
    ctx = _agent_context(stream=False, result=response)

    async def call_next() -> None:
        ctx.result = response

    await GroundingVerificationMiddleware().process(ctx, call_next)

    assert '"price":49.99' in response.text
    assert pid in response.text  # 原地修正，而非删除。
