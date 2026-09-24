"""验证费用估算实际成为可读取的指标。

使用真实内存 MeterProvider，而非模拟方法调用，确保指标未错误绑定
空操作提供器；只证明创建动作发生不足以证明读者能看到数值。
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from shared import metrics as cost_metrics
from shared.config import settings


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    """安装真实 SDK MeterProvider，返回对应读取器。"""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    # 在项目接口处替换，不修改 OTel 全局提供器。
    # 全局 set_meter_provider 只能设置一次，
    # 会污染后续测试。
    import shared.telemetry

    monkeypatch.setattr(shared.telemetry, "get_meter", provider.get_meter)
    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    cost_metrics._reset_for_tests()
    yield reader
    cost_metrics._reset_for_tests()


def _points(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    out = []
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    out.extend(metric.data.data_points)
    return out


def test_a_priced_turn_reaches_a_reader(reader: InMemoryMetricReader) -> None:
    cost_metrics.record_llm_turn_cost(
        0.0042, model="gpt-4.1", tokens_in=1200, tokens_out=300, agent="orchestrator", mode="observe"
    )

    points = _points(reader, "ecommerce.llm.cost.usd")
    assert len(points) == 1
    assert points[0].value == pytest.approx(0.0042)
    assert points[0].attributes["model"] == "gpt-4.1"
    assert points[0].attributes["agent"] == "orchestrator"


def test_turns_accumulate_rather_than_overwrite(reader: InMemoryMetricReader) -> None:
    """费用在进程内单调累加，用 Counter 便于按窗口差值告警。"""
    for _ in range(3):
        cost_metrics.record_llm_turn_cost(0.01, model="gpt-4.1", tokens_in=10, tokens_out=5)

    points = _points(reader, "ecommerce.llm.cost.usd")
    assert len(points) == 1
    assert points[0].value == pytest.approx(0.03)


def test_tokens_are_split_by_direction(reader: InMemoryMetricReader) -> None:
    """同时记录原始 token 数，区分用量变化和价格表变化。"""
    cost_metrics.record_llm_turn_cost(0.01, model="gpt-4.1", tokens_in=1000, tokens_out=250)

    by_direction = {p.attributes["direction"]: p.value for p in _points(reader, "ecommerce.llm.tokens")}
    assert by_direction == {"input": 1000, "output": 250}


def test_no_user_scoped_attribute_is_ever_attached(reader: InMemoryMetricReader) -> None:
    """禁止按客户创建指标序列，避免高基数成本和身份泄露。"""
    cost_metrics.record_llm_turn_cost(
        0.01, model="gpt-4.1", tokens_in=10, tokens_out=5, agent="orchestrator", mode="observe"
    )

    attributes = set(_points(reader, "ecommerce.llm.cost.usd")[0].attributes)
    assert attributes == {"model", "agent", "mode"}


def test_recording_is_a_no_op_when_telemetry_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """遥测关闭时不能创建指标，避免绑定尚未初始化的空操作提供器。"""
    monkeypatch.setattr(settings, "OTEL_ENABLED", False)
    cost_metrics._reset_for_tests()

    cost_metrics.record_llm_turn_cost(0.01, model="gpt-4.1", tokens_in=10, tokens_out=5)

    assert cost_metrics._instruments is None


def test_a_broken_metrics_backend_cannot_fail_a_request(
    reader: InMemoryMetricReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接收端配置错误不能导致客户请求失败。"""
    instruments = cost_metrics._get_instruments()

    class _Exploding:
        def add(self, *_args, **_kwargs):
            raise RuntimeError("collector unreachable")

    monkeypatch.setitem(instruments, "cost", _Exploding())

    cost_metrics.record_llm_turn_cost(0.01, model="gpt-4.1", tokens_in=10, tokens_out=5)
