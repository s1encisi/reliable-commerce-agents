"""本机／共享卷上的累计调用预算；预留在网络请求之前，未知费用不释放。

同一活动的所有进程必须使用同一路径。flock 仅适用于支持 POSIX 锁的共享
文件系统；多主机部署应使用数据库实现，不能各自使用本地副本。
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

LIMITS = {"deepseek": ("CNY", 50_000_000, 1000), "moonshot": ("CNY", 20_000_000, 8), "jev": ("USD", 1_000_000, 100)}


class BudgetError(RuntimeError):
    """预算、价格或账本状态不允许开始新调用。"""


@dataclass(frozen=True)
class Price:
    """每百万 token 的本币单价；输入价含最坏情况下的缓存写入费用。"""

    currency: str
    input_per_million: Decimal
    output_per_million: Decimal
    version: str
    valid_until: str | None = None

    def assert_current(self) -> None:
        from datetime import UTC, date, datetime

        if self.valid_until is not None and datetime.now(UTC).date() > date.fromisoformat(self.valid_until):
            raise BudgetError("价格核验已过期，未发送新请求")

    def cost(self, inputs: int, outputs: int) -> int:
        if inputs < 0 or outputs < 0:
            raise BudgetError("用量不能为负")
        # 本币微单位；百万 token 的分母与微单位换算相互抵消。
        return int(
            (inputs * self.input_per_million + outputs * self.output_per_million).to_integral_value(
                rounding=ROUND_CEILING
            )
        )

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> Price:
        try:
            price = cls(
                value["currency"],
                Decimal(str(value["input_per_million"])),
                Decimal(str(value["output_per_million"])),
                value["version"],
                value.get("valid_until"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise BudgetError("缺少已核验的价格配置") from exc
        if not price.version or not all(
            x.is_finite() and x >= 0 for x in (price.input_per_million, price.output_per_million)
        ):
            raise BudgetError("价格配置非法")
        if price.input_per_million == price.output_per_million == 0:
            raise BudgetError("不能把未知价格记为免费")
        return price


class CampaignBudget:
    """跨进程原子预留；没有超时自动回收，重启不能恢复额度。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()

    @contextmanager
    def _locked(self):
        if os.name != "posix":
            raise BudgetError("累计账本需要 POSIX 文件锁，请在 WSL/Linux 中运行付费客户端")
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        with lock.open("a+") as handle:
            os.chmod(lock, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            if self.path.exists():
                try:
                    state = json.loads(self.path.read_text())
                except (ValueError, OSError) as exc:
                    raise BudgetError("账本损坏，禁止重置或继续付费调用") from exc
                if state.get("schema") != 1 or not isinstance(state.get("attempts"), dict):
                    raise BudgetError("账本格式不兼容")
            else:
                state = {"schema": 1, "attempts": {}, "halted": []}
            for row in state["attempts"].values():
                if not isinstance(row, dict) or row.get("provider") not in LIMITS:
                    raise BudgetError("账本记录非法")
                if any(type(row.get(key)) is not int or row[key] < 0 for key in ("reserved", "charged")):
                    raise BudgetError("账本费用非法")
                if row.get("currency") != LIMITS[row["provider"]][0]:
                    raise BudgetError("账本币种非法")
            yield state
            fd, temp = tempfile.mkstemp(prefix=".budget-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w") as output:
                    json.dump(state, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temp, self.path)
                directory = os.open(self.path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)

    def reserve(
        self,
        provider: str,
        model: str,
        amount: int,
        *,
        currency: str,
        purpose: str,
        price_version: str,
        run_id: str = "",
        max_run_calls: int = 12,
    ) -> str:
        if provider not in LIMITS or not isinstance(amount, int) or amount <= 0:
            raise BudgetError("提供方或费用上界无效")
        expected_currency, cap, max_calls = LIMITS[provider]
        if currency != expected_currency:
            raise BudgetError("预算币种与价格币种不一致")
        with self._locked() as state:
            rows = [x for x in state["attempts"].values() if x["provider"] == provider]
            used = sum(x["charged"] for x in rows)
            if provider in state["halted"] or len(rows) >= max_calls or used + amount > cap:
                raise BudgetError("累计额度或调用次数已达上限")
            if purpose == "evaluation" and used >= cap * 85 // 100:
                raise BudgetError("已达 85%，停止扩样")
            if run_id:
                limits = state.setdefault("run_limits", {})
                max_run_calls = min(max_run_calls, limits.setdefault(run_id, max_run_calls))
            if run_id and sum(x.get("run_id") == run_id for x in state["attempts"].values()) >= max_run_calls:
                raise BudgetError("本次运行的跨服务调用次数已达上限")
            attempt = str(uuid4())
            state["attempts"][attempt] = {
                "provider": provider,
                "model": model,
                "currency": currency,
                "purpose": purpose,
                "price_version": price_version,
                "run_id": run_id,
                "reserved": amount,
                "charged": amount,
                "status": "reserved",
                "usage": None,
            }
            return attempt

    def settle(self, attempt: str, actual: int | None, usage: dict[str, int] | None = None) -> None:
        if actual is not None and (not isinstance(actual, int) or actual < 0):
            raise BudgetError("实际费用无效")
        with self._locked() as state:
            row = state["attempts"][attempt]
            if row["status"] == "confirmed":
                if actual != row["charged"]:
                    raise BudgetError("已确认回执不能改写")
                return
            row["status"] = "unknown" if actual is None else "confirmed"
            row["usage"] = usage
            if actual is not None:
                row["charged"] = actual
                if actual > row["reserved"] and row["provider"] not in state["halted"]:
                    state["halted"].append(row["provider"])

    def halt(self, provider: str) -> None:
        """认证、余额或配置错误后停止该提供方；不清除历史费用。"""
        with self._locked() as state:
            if provider not in state["halted"]:
                state["halted"].append(provider)

    def snapshot(self) -> dict[str, Any]:
        with self._locked() as state:
            totals = {}
            for provider, (currency, cap, maximum) in LIMITS.items():
                rows = [x for x in state["attempts"].values() if x["provider"] == provider]
                totals[provider] = {
                    "currency": currency,
                    "cap": cap / 1e6,
                    "committed_or_reserved": sum(x["charged"] for x in rows) / 1e6,
                    "input_tokens": sum((x.get("usage") or {}).get("input_tokens", 0) for x in rows),
                    "output_tokens": sum((x.get("usage") or {}).get("output_tokens", 0) for x in rows),
                    "calls": len(rows),
                    "max_calls": maximum,
                    "unknown": sum(x["status"] != "confirmed" for x in rows),
                }
            return totals
