"""教程的模型响应录制与回放客户端。

replay 默认读取章节 tests/fixtures/replay，无网络和凭据。只有
RECORD=true 且夹具缺失时，才经指定真实提供方录制，需相应授权。

每轮按有序消息、系统指令及工具模式计算请求哈希。组合真实
FunctionInvocationLayer，使回放仍执行本地工具并推进下一轮。
录制直接读取底层单轮响应，保留 function_call，不能提前执行完整循环。
流式输出按消息分块，不是逐 token。

应用端另有独立实现，读取 Settings；教程读取环境变量且不依赖
应用工作区。公共机制同步维护，数据库易变值归一化仅存在于应用端。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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
    """回放缺少夹具时抛出；record=True 则改为真实调用。"""


def _canonical_request(messages: Any, options: dict[str, Any] | None) -> dict[str, Any]:
    """生成包含消息和工具模式的可序列化请求视图。

    采样参数不参与键，避免无关配置调整使全部夹具失效。
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
        # 系统指令位于 options，
        # 也必须加入哈希，防止不同指令
        # 错误命中同一夹具。
        "instructions": (options or {}).get("instructions"),
    }


def _request_hash(canonical: dict[str, Any]) -> str:
    blob = json.dumps(canonical, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class ReplayChatClient(FunctionInvocationLayer, BaseChatClient):
    """用录制夹具代替真实模型的客户端，并保留实际工具执行层。"""

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
        """仅录制缺失夹具时按需构造真实客户端。

        与各章默认客户端一样直接读取环境变量，教程没有应用端配置单例。
        """
        if self._record_client is not None:
            return self._record_client

        provider = self._record_provider.lower()
        if provider == "azure":
            from agent_framework.openai import OpenAIChatCompletionClient

            endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
            key = os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
            deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
            if not (endpoint and key and deployment):
                raise ValueError(
                    "RECORD=true with REPLAY_RECORD_PROVIDER=azure requires AZURE_OPENAI_ENDPOINT, "
                    "AZURE_OPENAI_KEY, and AZURE_OPENAI_DEPLOYMENT in the repo-root .env."
                )
            self._record_client = OpenAIChatCompletionClient(
                model=deployment,
                azure_endpoint=endpoint,
                api_key=key,
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            )
        elif provider == "openai":
            from agent_framework.openai import OpenAIChatClient

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "RECORD=true with REPLAY_RECORD_PROVIDER=openai requires OPENAI_API_KEY in the repo-root .env."
                )
            self._record_client = OpenAIChatClient(
                model=os.environ.get("LLM_MODEL", "gpt-4.1"),
                api_key=api_key,
                base_url=os.environ.get("LLM_BASE_URL") or None,
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
            # 将分块重新合成为
            # ChatResponse。裸 ResponseStream 虽可迭代，
            # 但 MAF 工作流内部
            # 还会调用最终响应接口，
            # 没有终结器时 get_final_response()
            # 会抛错，而非返回预期结果。
            return self._build_response_stream(_gen())

        return self._load_or_record(messages, options)
