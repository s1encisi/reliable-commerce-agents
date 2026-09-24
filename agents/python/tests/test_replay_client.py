"""回放客户端测试。

单元测试替换录制客户端，无网络或凭据；可选真实模型集成测试
单独验证录制再回放链路。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from agent_framework import Agent, ChatResponse, Content, Message, tool

from shared.replay_client import ReplayChatClient, ReplayFixtureMissingError, _canonical_request, _request_hash


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        messages=[Message(role="assistant", contents=[Content.from_text(text=text)])],
        response_id=str(uuid.uuid4()),
        finish_reason="stop",
    )


class _StubRecordClient:
    """录制路径真实客户端的替身。"""

    def __init__(self, *responses: ChatResponse) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def _inner_get_response(self, *, messages: Any, stream: bool, options: dict[str, Any]) -> ChatResponse:
        self.calls += 1
        if not self._responses:
            raise AssertionError("stub record client ran out of canned responses")
        return self._responses.pop(0)


# ─────────────────────── hashing ───────────────────────


def test_canonical_request_excludes_sampling_params() -> None:
    messages = [Message(role="user", contents=["hello"])]
    canonical = _canonical_request(messages, {"temperature": 0.9, "max_tokens": 100})
    assert "temperature" not in canonical
    assert "max_tokens" not in canonical
    assert canonical["messages"][0]["contents"][0]["text"] == "hello"


def test_request_hash_is_stable_and_order_sensitive() -> None:
    m1 = [Message(role="user", contents=["a"])]
    m2 = [Message(role="user", contents=["a"])]
    m3 = [Message(role="user", contents=["b"])]
    assert _request_hash(_canonical_request(m1, {})) == _request_hash(_canonical_request(m2, {}))
    assert _request_hash(_canonical_request(m1, {})) != _request_hash(_canonical_request(m3, {}))


# ─────────────────────── record / replay mechanics ───────────────────────


@pytest.mark.asyncio
async def test_replay_raises_when_no_fixture_and_not_recording(tmp_path) -> None:
    client = ReplayChatClient(fixtures_dir=tmp_path, record=False)
    with pytest.raises(ReplayFixtureMissingError, match="RECORD=true"):
        await client._inner_get_response(messages=[Message(role="user", contents=["hi"])], stream=False, options={})


@pytest.mark.asyncio
async def test_record_writes_fixture_and_replay_reads_it_back(tmp_path) -> None:
    stub = _StubRecordClient(_text_response("Paris"))
    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True)
    recorder._record_client = stub  # 绕过真实客户端构建。

    messages = [Message(role="user", contents=["capital of France?"])]
    recorded_response = await recorder._inner_get_response(messages=messages, stream=False, options={})
    assert recorded_response.text == "Paris"
    assert stub.calls == 1

    fixtures = list(tmp_path.glob("*.json"))
    assert len(fixtures) == 1
    saved = json.loads(fixtures[0].read_text())
    assert saved["response"]["messages"][0]["contents"][0]["text"] == "Paris"

    replayer = ReplayChatClient(fixtures_dir=tmp_path, record=False)
    replayed_response = await replayer._inner_get_response(messages=messages, stream=False, options={})
    assert replayed_response.text == "Paris"


@pytest.mark.asyncio
async def test_replay_never_calls_the_record_client_when_fixture_exists(tmp_path) -> None:
    messages = [Message(role="user", contents=["hi"])]
    stub = _StubRecordClient(_text_response("first"), _text_response("SHOULD NOT BE USED"))
    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True)
    recorder._record_client = stub
    await recorder._inner_get_response(messages=messages, stream=False, options={})
    assert stub.calls == 1

    replayer = ReplayChatClient(fixtures_dir=tmp_path, record=True)  # 允许录制，但夹具已存在。
    replayer._record_client = stub
    result = await replayer._inner_get_response(messages=messages, stream=False, options={})
    assert result.text == "first"
    assert stub.calls == 1, "existing fixture must short-circuit before touching the record client"


@pytest.mark.asyncio
async def test_streaming_replays_as_a_single_chunk_per_message(tmp_path) -> None:
    stub = _StubRecordClient(_text_response("streamed answer"))
    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True)
    recorder._record_client = stub
    await recorder._inner_get_response(messages=[Message(role="user", contents=["x"])], stream=False, options={})

    replayer = ReplayChatClient(fixtures_dir=tmp_path, record=False)
    stream = replayer._inner_get_response(messages=[Message(role="user", contents=["x"])], stream=True, options={})
    chunks = [c async for c in stream]
    assert len(chunks) == 1
    assert chunks[0].text == "streamed answer"


@pytest.mark.asyncio
async def test_streaming_response_supports_get_final_response(tmp_path) -> None:
    """流式最终响应终结器回归测试。

    工作流内部不仅迭代分块，还会调用 get_final_response；裸生成器
    包装若未安装终结器会抛错。
    """
    stub = _StubRecordClient(_text_response("collapsed answer"))
    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True)
    recorder._record_client = stub
    await recorder._inner_get_response(messages=[Message(role="user", contents=["x"])], stream=False, options={})

    replayer = ReplayChatClient(fixtures_dir=tmp_path, record=False)
    stream = replayer._inner_get_response(messages=[Message(role="user", contents=["x"])], stream=True, options={})
    final = await stream.get_final_response()
    assert final.text == "collapsed answer"


# ─────────────────────── tool-calling loop, end to end via Agent ───────────────────────


@pytest.mark.asyncio
async def test_agent_replays_a_tool_calling_loop(tmp_path) -> None:
    """两轮回放：先函数调用，再最终文本。

    验证回放期间确实执行真实本地工具，而非仅输出录制文本。
    """

    @tool(name="get_weather", description="Get weather for a city")
    async def get_weather(city: str) -> str:
        return f"sunny in {city}"

    call_response = ChatResponse(
        messages=[
            Message(
                role="assistant",
                contents=[
                    Content.from_function_call(call_id="call_1", name="get_weather", arguments={"city": "Paris"})
                ],
            )
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )
    final_response = _text_response("It's sunny in Paris!")

    stub = _StubRecordClient(call_response, final_response)
    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True)
    recorder._record_client = stub
    agent = Agent(client=recorder, instructions="test", tools=[get_weather])
    recorded_result = await agent.run("What's the weather in Paris?")
    assert "sunny" in recorded_result.text.lower()
    assert stub.calls == 2
    assert len(list(tmp_path.glob("*.json"))) == 2

    replayer = ReplayChatClient(fixtures_dir=tmp_path, record=False)
    replay_agent = Agent(client=replayer, instructions="test", tools=[get_weather])
    replayed_result = await replay_agent.run("What's the weather in Paris?")
    assert replayed_result.text == recorded_result.text


# ─────────────────────── real provider, gated ───────────────────────


def _available_provider() -> str | None:
    """直接检查提供方是否有可用凭据，避免依赖其他测试可能替换的配置单例。"""
    from shared.config import settings

    if settings.AZURE_OPENAI_KEY and settings.AZURE_OPENAI_ENDPOINT:
        return "azure"
    if settings.OPENAI_API_KEY and not settings.OPENAI_API_KEY.startswith("sk-your-"):
        return "openai"
    return None


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(_available_provider() is None, reason="no LLM credentials in .env")
async def test_record_against_real_provider_then_replay_offline(tmp_path) -> None:
    provider = _available_provider()
    assert provider is not None  # skipif 已保证该条件，这里进一步明确类型。
    recorder = ReplayChatClient(fixtures_dir=tmp_path, record=True, record_provider=provider)
    agent = Agent(client=recorder, instructions="Answer with exactly one word.")
    real_answer = await agent.run("What is the capital of France?")
    assert "paris" in real_answer.text.lower()
    assert len(list(tmp_path.glob("*.json"))) == 1

    # 清空录制所需凭据，回放不得读取它们或发起请求。
    saved_key, saved_endpoint = os.environ.get("AZURE_OPENAI_KEY"), os.environ.get("AZURE_OPENAI_ENDPOINT")
    saved_openai_key = os.environ.get("OPENAI_API_KEY")
    for var in ("AZURE_OPENAI_KEY", "AZURE_OPENAI_ENDPOINT", "OPENAI_API_KEY"):
        os.environ.pop(var, None)
    try:
        replayer = ReplayChatClient(fixtures_dir=tmp_path, record=False)
        replay_agent = Agent(client=replayer, instructions="Answer with exactly one word.")
        replayed_answer = await replay_agent.run("What is the capital of France?")
        assert replayed_answer.text == real_answer.text
    finally:
        if saved_key is not None:
            os.environ["AZURE_OPENAI_KEY"] = saved_key
        if saved_endpoint is not None:
            os.environ["AZURE_OPENAI_ENDPOINT"] = saved_endpoint
        if saved_openai_key is not None:
            os.environ["OPENAI_API_KEY"] = saved_openai_key


# ─────────────────── hash normalization (issue #25) ────────────────────


def _tool_msg(payload: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "contents": [{"type": "function_result", "call_id": "call_1", "result": payload}],
    }


def _user_msg(text: str) -> dict[str, Any]:
    return {"role": "user", "contents": [{"type": "text", "text": text}]}


def _canonical(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"messages": messages, "tools": [], "instructions": "be helpful"}


def test_tool_results_hash_the_same_across_a_reseed():
    """数据库重新初始化后的 UUID 与时间戳变化不能使同一请求夹具失效。"""
    session_a = _canonical(
        [
            _user_msg("What do reviews say about the Sony WH-1000XM5?"),
            _tool_msg(
                '{"review_id": "e9418342-8843-4b1b-ab01-e63fe2e8b8f0", '
                '"rating": 5, "date": "2026-05-18T21:27:13.100156+00:00"}'
            ),
        ]
    )
    session_b = _canonical(
        [
            _user_msg("What do reviews say about the Sony WH-1000XM5?"),
            _tool_msg(
                '{"review_id": "bf88ec9f-1137-4de9-b0ef-d2a50a29513d", '
                '"rating": 5, "date": "2026-05-19T09:02:44.881003+00:00"}'
            ),
        ]
    )

    assert _request_hash(session_a) == _request_hash(session_b)


def test_different_questions_still_hash_differently():
    """归一化不能混淆真正不同的请求。"""
    a = _canonical([_user_msg("Reviews for the Sony WH-1000XM5?")])
    b = _canonical([_user_msg("Reviews for the Dyson V15?")])

    assert _request_hash(a) != _request_hash(b)


def test_tool_call_arguments_are_not_normalized():
    """助手消息中的工具调用参数按原样哈希，不同查询不能共用夹具。"""

    def assistant_call(order_id: str) -> dict[str, Any]:
        return {
            "role": "assistant",
            "contents": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "get_order",
                    "arguments": {"order_id": order_id},
                }
            ],
        }

    a = _canonical([_user_msg("track it"), assistant_call("550e8400-e29b-41d4-a716-446655440001")])
    b = _canonical([_user_msg("track it"), assistant_call("550e8400-e29b-41d4-a716-446655440002")])

    assert _request_hash(a) != _request_hash(b)


def test_calendar_month_buckets_hash_the_same_as_the_calendar_advances():
    """自然月聚合随日期变化的回归测试。

    相同评论集合可能落入不同月份数量的桶；仅清理标签不足以稳定键，
    还要处理特定工具的易变聚合结构。两个载荷只因日历推进而不同。
    """
    seven_buckets = _canonical(
        [
            _user_msg("How has sentiment for the Sony WH-1000XM5 changed?"),
            _tool_msg(
                '{"product_name": "Sony WH-1000XM5", "period_months": 6, "trend": "declining", '
                '"monthly_data": [{"month": "2026-02", "average_rating": 5.0, "review_count": 1}, '
                '{"month": "2026-03", "average_rating": 4.0, "review_count": 2}, '
                '{"month": "2026-08", "average_rating": 4.67, "review_count": 3}]}'
            ),
        ]
    )
    five_buckets = _canonical(
        [
            _user_msg("How has sentiment for the Sony WH-1000XM5 changed?"),
            _tool_msg(
                '{"product_name": "Sony WH-1000XM5", "period_months": 6, "trend": "stable", '
                '"monthly_data": [{"month": "2026-03", "average_rating": 4.5, "review_count": 2}, '
                '{"month": "2026-08", "average_rating": 4.67, "review_count": 3}]}'
            ),
        ]
    )

    assert _request_hash(seven_buckets) == _request_hash(five_buckets)


def test_the_bucket_scrub_does_not_blur_different_products():
    """聚合清理只针对指定字段，其他信息必须继续区分不同趋势查询。"""
    sony = _canonical(
        [
            _user_msg("trend?"),
            _tool_msg('{"product_name": "Sony WH-1000XM5", "trend": "declining", "monthly_data": []}'),
        ]
    )
    dyson = _canonical(
        [
            _user_msg("trend?"),
            _tool_msg('{"product_name": "Dyson V15", "trend": "declining", "monthly_data": []}'),
        ]
    )

    assert _request_hash(sony) != _request_hash(dyson)


def test_non_volatile_tool_payload_differences_still_matter():
    """只清理 UUID 与时间戳等易变字段，保留真实数据差异。"""
    a = _canonical([_user_msg("stock?"), _tool_msg('{"in_stock": true, "quantity": 12}')])
    b = _canonical([_user_msg("stock?"), _tool_msg('{"in_stock": false, "quantity": 0}')])

    assert _request_hash(a) != _request_hash(b)


def test_every_committed_fixture_rehashes_to_its_own_filename():
    """每个夹具文件名必须能由保存的原请求重新计算。

    哈希方案变更后若未重新计算夹具键，测试应立即失败。
    """
    fixtures_dir = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "replay"
    fixtures = sorted(fixtures_dir.glob("*.json"))
    assert fixtures, f"no fixtures found in {fixtures_dir}"

    mismatched = [f.name for f in fixtures if _request_hash(json.loads(f.read_text())["request"]) != f.stem]
    assert not mismatched, (
        f"{len(mismatched)} fixture(s) no longer hash to their own filename "
        f"(first few: {mismatched[:5]}). Run: uv run python -m evals.rehash_fixtures"
    )


def test_normalization_does_not_merge_distinct_committed_requests():
    """所有已提交夹具必须保持不同键，防止过度归一化静默返回错误答案。"""
    fixtures_dir = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "replay"
    by_hash: dict[str, list[str]] = {}
    for f in sorted(fixtures_dir.glob("*.json")):
        by_hash.setdefault(_request_hash(json.loads(f.read_text())["request"]), []).append(f.name)

    collisions = {h: names for h, names in by_hash.items() if len(names) > 1}
    assert not collisions, f"normalization merged distinct fixtures: {collisions}"


def test_deterministic_product_ids_are_load_bearing_for_this_design():
    """只归一化工具结果依赖调用参数中的标识稳定。

    商品使用确定性 uuid5；若数据集把随机订单或评论 UUID 作为后续
    工具参数，就会再次使未归一化的助手消息变化。
    """
    fixtures_dir = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "replay"
    uuid_re = re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-([0-9a-fA-F])[0-9a-fA-F]{3}"
        r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    )
    # 查无结果用例使用硬编码合成标识，
    # 即使形态像 UUID4，也保持稳定。
    synthetic = "550e8400-e29b-41d4-a716-"

    offenders: list[str] = []
    for f in sorted(fixtures_dir.glob("*.json")):
        for message in json.loads(f.read_text())["request"]["messages"]:
            if message.get("role") == "tool":
                continue
            blob = json.dumps(message)
            if any(
                version != "5"
                for match, version in ((m.group(0), m.group(1)) for m in uuid_re.finditer(blob))
                if not match.startswith(synthetic)
            ):
                offenders.append(f.name)
                break

    assert not offenders, (
        "non-deterministic UUIDs found outside tool-result messages in "
        f"{offenders[:5]} — see _normalize_for_hash's docstring"
    )
