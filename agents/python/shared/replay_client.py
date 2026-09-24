"""录制和回放聊天客户端，用固定模型响应驱动真实智能体流程。

LLM_PROVIDER=replay 默认只读取 REPLAY_FIXTURES_DIR，无需网络或密钥。
RECORD=true 时，缺少夹具才使用 REPLAY_RECORD_PROVIDER 发起真实调用
并保存结果；录制仍需明确授权和相应凭据。

夹具按有序消息、系统指令及工具模式构成的请求哈希索引，每次模型
调用对应一个文件。FunctionInvocationLayer 仍执行真实本地工具，
再把结果加入下一轮；因此回放覆盖工具循环，而非只重放最终答案。
录制直接调用真实客户端 _inner_get_response，避免真实客户端的工具层
提前执行完整循环，丢失原始 function_call。

流式模式按消息输出完整块，不是逐 token 流。教程有独立副本，因其
环境不依赖 agents/python；公共行为应同步，但教程不含数据库载荷，
有意不使用应用端的 _normalize_for_hash，以免改变已有夹具键。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    FunctionInvocationLayer,
)

logger = logging.getLogger(__name__)


class ReplayFixtureMissingError(RuntimeError):
    """回放模式缺少请求夹具时抛出。

    record=True 时改为发起真实调用，不抛此异常。
    """


def _canonical_request(messages: Any, options: dict[str, Any] | None) -> dict[str, Any]:
    """将请求消息和工具模式转为可 JSON 序列化、可哈希的结构。

    温度等采样参数不参与键，避免不影响请求语义的配置调整使夹具失效。
    """
    tools = (options or {}).get("tools") or []
    tool_specs: list[dict[str, Any]] = []
    for t in tools:
        try:
            tool_specs.append(t.to_json_schema_spec())
        except AttributeError:
            tool_specs.append({"name": getattr(t, "name", str(t))})
    return {
        "messages": [m.to_dict() for m in messages],
        "tools": tool_specs,
        # 智能体系统指令保存在 options 而非消息中，
        # 也必须加入哈希，避免相同问题、不同指令
        # 错误命中同一夹具。
        "instructions": (options or {}).get("instructions"),
    }


# 工具结果中的数据库易变值，
# 每次重新初始化数据都可能不同，
# 因此由 _normalize_for_hash 单独处理。
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# 正则锚定且优先最长匹配，先消费完整时间戳，
# 避免年月分支只吃掉 YYYY-MM 前缀。
# 情感趋势按月分桶，
# 其 YYYY-MM 标签也会随初始化日期变化。
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}"  # 年月。
    r"(?:-\d{2}"  # 可选日期。
    r"(?:[T ]\d{2}:\d{2}:\d{2}"  # 可选时分秒。
    r"(?:\.\d+)?"  # 可选小数秒。
    r"(?:Z|[+-]\d{2}:?\d{2})?)?"  # 可选时区。
    r")?\b"
)

# 仅清理月份标签不够，桶结构本身也会变化，
# 其中普通数值无法用时间正则识别。
#
# get_sentiment_trend 按自然月分组，
# 时间窗口相对 NOW()；种子脚本按固定天数偏移生成评论，
# 随机种子固定了偏移，
# 窗口内评论集合因此可以保持不变，
# 但偏移落在哪个自然月会随日期变化。
# 相同评论可能分到不同数量的桶，
# 各桶计数、均值及推导趋势
# 也会随之改变。
#
# 若把这些结果直接纳入键，夹具就依赖墙钟日期，
# 可能连续通过数周后突然失效，
# 即使没有任何代码改动，
# 也会让排查者误以为出现代码回归。
#
# 这里只针对该自然月聚合工具清理易变结构。
# 其他相对时间查询返回的集合或标量通常稳定，
# 所以归一化必须保持范围狭窄，
# 不能粗暴清除所有数值，
# 否则真正不同的请求也会碰撞。
_MONTH_BUCKETS_RE = re.compile(r'"monthly_data"\s*:\s*\[[^\]]*\]')
_TREND_RE = re.compile(r'"trend"\s*:\s*"(?:improving|declining|stable|insufficient_data)"')


def _scrub(value: Any) -> Any:
    """递归将 UUID、ISO-8601 时间戳和自然月聚合替换为占位值。"""
    if isinstance(value, str):
        value = _MONTH_BUCKETS_RE.sub('"monthly_data": "<buckets>"', value)
        value = _TREND_RE.sub('"trend": "<trend>"', value)
        return _TIMESTAMP_RE.sub("<ts>", _UUID_RE.sub("<uuid>", value))
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


def _ordinalize_call_ids(messages: list[Any]) -> list[Any]:
    """将提供方生成的工具 call_id 按首次出现顺序归一化。

    依次映射为 call_0、call_1 等，保持调用与结果配对，避免重新录制
    前一轮就改变所有后续夹具键。调用侧和结果侧同时处理，也避免仅在
    工具结果中清理 UUID 时破坏配对。
    """
    seen: dict[str, str] = {}
    out: list[Any] = []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("contents"), list):
            out.append(message)
            continue
        contents = []
        for content in message["contents"]:
            call_id = content.get("call_id") if isinstance(content, dict) else None
            if call_id is None:
                contents.append(content)
                continue
            contents.append({**content, "call_id": seen.setdefault(call_id, f"call_{len(seen)}")})
        out.append({**message, "contents": contents})
    return out


def _normalize_for_hash(canonical: dict[str, Any]) -> dict[str, Any]:
    """从夹具键中移除数据库派生的易变值。

    工具循环的下一轮携带上一轮数据库结果；重新初始化会改变 UUID 与
    时间字段，导致相同业务请求无法命中。这里只对需要处理的载荷归一化，
    保留真正的请求差异。教程不访问数据库，故不复制该逻辑，以免无谓
    改变全部教程夹具哈希。
    """
    messages = [
        _scrub(m) if isinstance(m, dict) and m.get("role") == "tool" else m for m in canonical.get("messages", [])
    ]
    return {**canonical, "messages": _ordinalize_call_ids(messages)}


def _request_hash(canonical: dict[str, Any]) -> str:
    blob = json.dumps(_normalize_for_hash(canonical), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class ReplayChatClient(FunctionInvocationLayer, BaseChatClient):
    """读取已录制夹具而不调用真实模型的 BaseChatClient。

    直接组合 FunctionInvocationLayer，保留真实工具循环。
    """

    OTEL_PROVIDER_NAME = "replay"

    def __init__(
        self,
        *,
        fixtures_dir: str | Path,
        record: bool = False,
        record_provider: str = "openai",
    ) -> None:
        super().__init__()
        self._fixtures_dir = Path(fixtures_dir)
        self._record = record
        self._record_provider = record_provider
        self._record_client: BaseChatClient | None = None

    def _build_record_client(self) -> BaseChatClient:
        """仅在缺少夹具且允许录制时创建真实客户端。

        不能调用通用 get_chat_client，否则当前 replay 配置会递归创建自身；
        直接按配置构造 openai 或 azure 客户端。
        """
        if self._record_client is not None:
            return self._record_client

        from shared.config import settings

        provider = self._record_provider.lower()
        if provider == "azure":
            from agent_framework.openai import OpenAIChatCompletionClient

            if not (settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_KEY and settings.AZURE_OPENAI_DEPLOYMENT):
                raise ValueError(
                    "RECORD=true with REPLAY_RECORD_PROVIDER=azure requires AZURE_OPENAI_ENDPOINT, "
                    "AZURE_OPENAI_KEY, and AZURE_OPENAI_DEPLOYMENT in .env."
                )
            self._record_client = OpenAIChatCompletionClient(
                model=settings.AZURE_OPENAI_DEPLOYMENT,
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
        elif provider == "openai":
            from agent_framework.openai import OpenAIChatClient

            if not settings.OPENAI_API_KEY:
                raise ValueError("RECORD=true with REPLAY_RECORD_PROVIDER=openai requires OPENAI_API_KEY in .env.")
            self._record_client = OpenAIChatClient(
                model=settings.LLM_MODEL,
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.LLM_BASE_URL or None,
            )
        else:
            raise ValueError(f"REPLAY_RECORD_PROVIDER must be 'openai' or 'azure', got {self._record_provider!r}")
        return self._record_client

    def _fixture_path(self, request_hash: str) -> Path:
        return self._fixtures_dir / f"{request_hash}.json"

    async def _load_or_record(self, messages: Any, options: dict[str, Any] | None) -> ChatResponse:
        canonical = _canonical_request(messages, options)
        request_hash = _request_hash(canonical)
        path = self._fixture_path(request_hash)

        if path.exists():
            data = json.loads(path.read_text())
            return ChatResponse.from_dict(data["response"])

        if not self._record:
            raise ReplayFixtureMissingError(
                f"No replay fixture for this request (hash={request_hash}) at {path}. "
                f"Run with RECORD=true to record it (needs real {self._record_provider} credentials)."
            )

        real_client = self._build_record_client()
        response = await real_client._inner_get_response(messages=messages, stream=False, options=options or {})
        self._fixtures_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"request": canonical, "response": response.to_dict()}, indent=2, sort_keys=True) + "\n"
        )
        logger.info("replay_client: recorded new fixture %s", path)
        return response

    def _inner_get_response(
        self,
        *,
        messages: Any,
        stream: bool,
        options: dict[str, Any] | None = None,
        **_: Any,
    ) -> Any:
        if stream:

            async def _gen():
                response = await self._load_or_record(messages, options)
                for msg in response.messages:
                    yield ChatResponseUpdate(role=msg.role, contents=msg.contents, author_name=msg.author_name)

            # 使用 _build_response_stream 安装终结器，
            # 把更新分块合成为最终 ChatResponse。
            # 仅创建裸 ResponseStream 虽可供简单迭代使用，
            # 但工作流中的 AgentExecutor 等调用方
            # 还会读取最终响应。
            # 若缺少终结器，get_final_response()
            # 会抛错，无法返回 ChatResponse。
            return self._build_response_stream(_gen())

        return self._load_or_record(messages, options)
