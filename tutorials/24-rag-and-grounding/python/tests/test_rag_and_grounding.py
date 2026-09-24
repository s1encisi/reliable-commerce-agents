"""
第 24 章 —— 检索与事实核验：测试。

- 单元测试直接检验检索（`search_products`）与核验
  （`extract_claims` / `verify_claims`）—— 不涉及 LLM。
- 智能体装配测试检验 `search_products` 已被注册。
- 回放测试回放一份已提交的夹具（若尚无夹具则优雅跳过）。
- 集成测试访问真实 LLM，缺少凭据时跳过。
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    CATALOG,
    FIXTURES_DIR,
    ProductClaim,
    ask,
    build_agent,
    extract_claims,
    search_products,
    verify_claims,
)

# ─────────────────── 检索单元测试（不涉及 LLM） ──────────────────


def test_search_products_matches_by_keyword() -> None:
    results = search_products.func("headphones")
    assert len(results) == 1
    assert results[0]["id"] == "P001"


def test_search_products_matches_by_category() -> None:
    results = search_products.func("electronics")
    ids = {p["id"] for p in results}
    assert ids == {"P001", "P004"}


def test_search_products_returns_empty_for_no_match() -> None:
    assert search_products.func("submarine") == []


def test_search_products_is_case_insensitive() -> None:
    assert search_products.func("HOODIE") == search_products.func("hoodie")


# ─────────────────── 事实核验单元测试（不涉及 LLM） ──────────────────


def test_extract_claims_finds_id_and_nearby_price() -> None:
    answer = "The Wireless Noise-Cancelling Headphones (P001) cost $129.99."
    claims = extract_claims(answer)
    assert claims == [ProductClaim(id="P001", price=129.99)]


def test_extract_claims_handles_id_with_no_price() -> None:
    claims = extract_claims("Product P002 is in stock.")
    assert claims == [ProductClaim(id="P002", price=None)]


def test_extract_claims_handles_multiple_ids() -> None:
    answer = "We have P001 at $129.99 and P004 at $39.99."
    claims = extract_claims(answer)
    assert [c.id for c in claims] == ["P001", "P004"]


def test_verify_claims_flags_correct_id_and_price_as_verified() -> None:
    report = verify_claims([ProductClaim(id="P001", price=129.99)])
    assert report.total_count == 1
    assert report.verified_count == 1
    assert report.verdicts[0].status == "verified"


def test_verify_claims_flags_price_mismatch() -> None:
    report = verify_claims([ProductClaim(id="P001", price=99.00)])
    assert report.verdicts[0].status == "price_mismatch"
    assert report.verified_count == 0


def test_verify_claims_flags_unknown_id_as_not_found() -> None:
    report = verify_claims([ProductClaim(id="P999", price=None)])
    assert report.verdicts[0].status == "not_found"


def test_verify_claims_ignores_claim_with_no_price_beyond_id_match() -> None:
    # 完全没有声称价格 —— 一个真实存在、未附价格的 id 判定为通过，
    # 因为没有任何东西与之不一致。
    report = verify_claims([ProductClaim(id="P003", price=None)])
    assert report.verdicts[0].status == "verified"


def test_catalog_has_expected_shape() -> None:
    assert len(CATALOG) == 5
    assert all({"id", "name", "price", "category"} <= p.keys() for p in CATALOG)


# ─────────────────── 智能体装配 ──────────────────


def test_agent_has_search_products_tool_registered() -> None:
    agent = build_agent(client=object())  # 该 client 不会被调用，这里只看结构
    tool_names = [getattr(t, "name", None) for t in agent.default_options.get("tools") or []]
    assert "search_products" in tool_names


# ─────────────────── 回放测试（无需凭据，可在 CI 中运行） ────


@pytest.mark.asyncio
async def test_replay_grounded_answer_names_a_real_product(monkeypatch: pytest.MonkeyPatch) -> None:
    """回放 tests/fixtures/replay/ —— 不走网络、不需凭据。

    曾针对真实 LLM 录制过一次（即下方的 test_real_llm_answer_is_grounded，
    以 RECORD=true 运行），随后提交入库。
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"{FIXTURES_DIR} 中没有已录制的夹具 —— 请先以 RECORD=true 运行")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    agent = build_agent()
    answer = await ask(agent, "Do you have any noise-cancelling headphones? What's the price and product id?")
    report = verify_claims(extract_claims(answer))
    assert report.total_count >= 1
    assert report.verified_count == report.total_count


# ─────────────────── 真实 LLM 集成测试 ────────────────


def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_real_llm_calls_search_products_tool() -> None:
    """LLM 应当使用检索工具，而不是凭记忆作答。"""
    agent = build_agent()
    answer = await ask(agent, "Do you have any noise-cancelling headphones? What's the price and product id?")
    assert "P001" in answer


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason=".env 中没有 LLM 凭据")
async def test_real_llm_answer_is_grounded() -> None:
    """真实回答中的每一条商品断言都必须能对照目录核验通过。"""
    agent = build_agent()
    answer = await ask(agent, "Do you have any noise-cancelling headphones? What's the price and product id?")
    report = verify_claims(extract_claims(answer))
    assert report.total_count >= 1, f"期望答案中至少有一条可核验的断言：{answer!r}"
    assert report.verified_count == report.total_count, f"存在未通过核验的断言：{answer!r} -> {report.verdicts}"
