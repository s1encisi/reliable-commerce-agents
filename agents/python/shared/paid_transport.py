"""在真实 HTTP 边界预留费用，每个请求单独计数；SDK 不得再隐藏重试。"""

from __future__ import annotations

import asyncio
import json
import time
from contextvars import ContextVar
from decimal import Decimal
from typing import Any

import httpx

from shared.campaign_budget import BudgetError, CampaignBudget, Price

current_root_run: ContextVar[str] = ContextVar("root_run_id", default="")
current_run_deadline: ContextVar[float | None] = ContextVar("run_deadline", default=None)
current_call_purpose: ContextVar[str] = ContextVar("call_purpose", default="execution")
HOSTS = {"deepseek": "api.deepseek.com", "moonshot": "api.moonshot.cn", "jev": "api.typesafe.ai"}


def configured_price(provider: str) -> Price:
    from shared.config import settings

    try:
        from datetime import UTC, date, datetime

        mapping = json.loads(settings.MODEL_PRICES_JSON)
        value = mapping[provider]
        if not value.get("valid_until") or datetime.now(UTC).date() > date.fromisoformat(value["valid_until"]):
            raise BudgetError("价格核验已过期，停止付费调用")
        return Price.from_mapping(value)
    except (ValueError, KeyError, TypeError) as exc:
        raise BudgetError(f"{provider} 未配置核验后的本币单价") from exc


def configured_budget() -> CampaignBudget:
    from shared.config import settings

    return CampaignBudget(settings.CAMPAIGN_BUDGET_PATH)


def usage_counts(payload: dict[str, Any]) -> tuple[int, int] | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    incoming = usage.get("prompt_tokens", usage.get("input_tokens"))
    outgoing = usage.get("completion_tokens", usage.get("output_tokens"))
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (incoming, outgoing)):
        return None
    return incoming, outgoing


class _MeteredStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, finish: Any, deadline: float) -> None:
        self.stream = stream
        self.finish = finish
        self.deadline = deadline
        self.buffer = b""
        self.usage: tuple[int, int] | None = None
        self.done = False

    async def __aiter__(self):
        try:
            iterator = self.stream.__aiter__()
            while True:
                remaining = self.deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError("模型流超过总截止时间")
                try:
                    chunk = await asyncio.wait_for(anext(iterator), remaining)
                except StopAsyncIteration:
                    break
                self.buffer += chunk
                while b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    if line.startswith(b"data:"):
                        try:
                            obj = json.loads(line[5:])
                            found = usage_counts(obj) if isinstance(obj, dict) else None
                            if found is not None:
                                self.usage = found
                        except (ValueError, TypeError):
                            pass
                if len(self.buffer) > 1_048_576:
                    raise BudgetError("模型响应帧超过预算缓冲上限")
                yield chunk
            await self._finish()
        finally:
            await self.aclose()

    async def _finish(self) -> None:
        if not self.done:
            self.done = True
            await asyncio.shield(self.finish(self.usage))

    async def aclose(self) -> None:
        try:
            await self.stream.aclose()
        finally:
            await self._finish()


class PaidTransport(httpx.AsyncBaseTransport):
    """只面向明确支持的端点，限制输出并原子预留最坏费用。"""

    def __init__(
        self,
        provider: str,
        *,
        budget: CampaignBudget | None = None,
        price: Price | None = None,
        inner: httpx.AsyncBaseTransport | None = None,
        max_output: int = 4096,
    ) -> None:
        self.provider = provider
        self.budget = budget or configured_budget()
        self.price = price or configured_price(provider)
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)
        self.max_output = max_output

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.price.assert_current()
        if request.url.scheme != "https" or request.url.host != HOSTS[self.provider]:
            raise BudgetError("付费客户端目标不是已授权的官方端点")
        allowed = "/v1/systemone" if self.provider == "jev" else "/chat/completions"
        if request.method != "POST" or not request.url.path.endswith(allowed):
            raise BudgetError("付费客户端仅允许已计价的推理端点")
        body = json.loads(await request.aread())
        purpose = current_call_purpose.get()
        if self.provider == "moonshot" and purpose not in {"planning", "probe"}:
            raise BudgetError("K3 仅允许复杂规划或一次连通性探测")
        if self.provider == "deepseek":
            body["model"] = "deepseek-flash"
            body["thinking"] = {"type": "disabled"}
            body.pop("reasoning_effort", None)
        elif self.provider == "moonshot":
            body["model"] = "kimi-k3"
            body["reasoning_effort"] = "high"
            for name in ("temperature", "top_p", "thinking", "presence_penalty", "frequency_penalty"):
                body.pop(name, None)
        else:
            body["model"] = "jev-1.13.0"
        output_limit = 0
        if self.provider != "jev":
            output_limit = min(
                int(body.get("max_tokens", body.get("max_completion_tokens", self.max_output))), self.max_output
            )
            if output_limit <= 0:
                raise BudgetError("输出上限无效")
            body.pop("max_completion_tokens", None)
            body["max_tokens"] = output_limit
            if body.get("stream"):
                body["stream_options"] = {"include_usage": True}
        raw = json.dumps(body, ensure_ascii=False).encode()
        # 按 UTF-8 字节数而非字符比例预留，再加协议开销；不假定缓存优惠。
        input_bound = len(raw) * 2 + 2048
        reserved = int(Decimal(self.price.cost(input_bound, output_limit)) * Decimal("1.15")) + 1
        from shared.config import settings

        deadline = current_run_deadline.get() or time.time() + 60.0
        if deadline is not None and time.time() >= deadline:
            raise BudgetError("本次运行的总截止时间已到")
        started = time.monotonic()
        attempt = await asyncio.to_thread(
            self.budget.reserve,
            self.provider,
            body["model"],
            reserved,
            currency=self.price.currency,
            purpose="evaluation" if settings.EVALUATION_MODE else purpose,
            price_version=self.price.version,
            run_id=current_root_run.get(),
            max_run_calls=settings.MODEL_MAX_CALLS_PER_RUN,
        )

        async def finish(counts: tuple[int, int] | None) -> None:
            usage = {"input_tokens": counts[0], "output_tokens": counts[1]} if counts is not None else None
            cost = self.price.cost(*counts) if counts is not None else None
            await asyncio.to_thread(self.budget.settle, attempt, cost, usage)
            from shared.upgrade_metrics import record_attempt

            record_attempt(
                self.provider,
                body["model"],
                "unknown" if counts is None else "confirmed",
                time.monotonic() - started,
                self.price.currency,
                (cost if cost is not None else reserved) / 1e6,
            )

        headers = dict(request.headers)
        headers.pop("content-length", None)
        forwarded = httpx.Request(
            request.method, request.url, headers=headers, content=raw, extensions=request.extensions
        )
        try:
            remaining = max(0.001, deadline - time.time()) if deadline is not None else 60.0
            response = await asyncio.wait_for(self.inner.handle_async_request(forwarded), remaining)
            if body.get("stream") and response.is_success:
                response.stream = _MeteredStream(response.stream, finish, deadline)
            else:
                remaining = max(0.001, deadline - time.time()) if deadline is not None else 60.0
                data = await asyncio.wait_for(response.aread(), remaining)
                if response.status_code in {400, 401, 402, 403, 404, 422}:
                    await asyncio.to_thread(self.budget.halt, self.provider)
                if not response.is_success:
                    from shared.context_pipeline import public_text

                    safe = public_text(data.decode("utf-8", errors="replace"))
                    credential = request.headers.get("authorization", "").removeprefix("Bearer ")
                    if credential:
                        safe = safe.replace(credential, "[credential]")
                    response._content = safe.encode()
                    response.stream = httpx.ByteStream(response._content)
                try:
                    result = json.loads(data)
                    counts = usage_counts(result) if isinstance(result, dict) else None
                except ValueError:
                    counts = None
                await finish(counts)
            return response
        except BaseException:
            # 包括取消：请求可能已被接收，因此保留预留，不直接重放业务。
            await asyncio.shield(finish(None))
            raise

    async def aclose(self) -> None:
        await self.inner.aclose()
