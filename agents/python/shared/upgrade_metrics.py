"""只记录低基数标签；原始问题、身份与推理文本不进入模型指标。"""

from functools import lru_cache

from shared.telemetry import get_meter


@lru_cache(maxsize=1)
def _instruments():
    meter = get_meter("commerce.upgrade")
    return (
        meter.create_counter("commerce.model.attempts"),
        meter.create_histogram("commerce.model.duration", unit="s"),
        meter.create_counter("commerce.model.cost_ceiling", unit="1"),
    )


def record_attempt(provider: str, model: str, status: str, duration: float, currency: str, ceiling: float) -> None:
    from shared.config import settings

    if not settings.OTEL_ENABLED:
        return
    try:
        attempts, latency, cost = _instruments()
        attrs = {"provider": provider, "model": model, "status": status, "currency": currency}
        attempts.add(1, attrs)
        latency.record(duration, attrs)
        cost.add(ceiling, attrs)
    except Exception:
        # 监控失败不能重发请求或丢失预算回执。
        pass
