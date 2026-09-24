"""预算内的真实决策评测入口；凭据只在本进程运行时输入。"""

import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT / ".local/upgrade-evaluation"
WORK.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "agents/python"))
os.environ.update(
    APP_ENV_FILE="/dev/null",
    OPENAI_API_KEY="",
    AZURE_OPENAI_KEY="",
    AZURE_OPENAI_API_KEY="",
    ENVIRONMENT="test",
    OTEL_ENABLED="false",
    RECORD="false",
)
os.environ["MODEL_PRICES_JSON"] = (ROOT / "agents/python/config/model-prices.json").read_text()


async def main():
    if not sys.stdin.isatty():
        raise SystemExit("安全终端输入不可用，未读取凭据")
    for provider, name in [
        ("deepseek", "DEEPSEEK_API_KEY"),
        ("moonshot", "MOONSHOT_API_KEY"),
        ("jev", "TYPESAFE_API_KEY"),
    ]:
        os.environ[name] = getpass.getpass(provider + " credential (hidden): ")
    from shared.jev.async_client import decide_route
    from shared.jev.decisions import SPECIALIST_ROUTES
    from shared.paid_transport import configured_budget, current_call_purpose

    dataset = json.loads((ROOT / "agents/python/evals/upgrade/dataset.json").read_text())
    result_path = WORK / "live-results.json"
    results = (
        json.loads(result_path.read_text())
        if result_path.exists()
        else {"dataset": dataset["version"], "development": [], "holdout": [], "http": [], "complex": []}
    )
    revision = os.environ.get("UPGRADE_EVAL_REVISION", "safety-fixes-v2")
    if results.get("http") and results.get("http_revision") != revision:
        results.setdefault("http_rounds", {})[results.get("http_revision", "pilot")] = results["http"]
        results["http"] = []
    results["http_revision"] = revision

    def save():
        results["budget"] = configured_budget().snapshot()
        result_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))

    current_call_purpose.set("evaluation")
    # 顺序调用；超时也记录，重启跳过已经尝试的样本，不重花同一批预算。
    for split in ["development", "holdout"]:
        for case_id, message, expected in dataset[split]:
            if any(x["id"] == case_id for x in results[split]):
                continue
            entry = {"id": case_id, "expected": expected}
            try:
                decision = await decide_route(
                    {"latest_message": message, "relevant_history": []}, set(SPECIALIST_ROUTES)
                )
                entry.update(
                    route=decision.route,
                    probabilities=decision.probabilities,
                    confidence=decision.confidence,
                    model=decision.model,
                    latency_ms=decision.latency_ms,
                    usage=decision.usage,
                    correct=decision.route == expected,
                )
            except Exception as exc:
                entry.update(error_type=type(exc).__name__, correct=False)
            results[split].append(entry)
            save()
            print(split, len(results[split]), entry.get("route", entry.get("error_type")), flush=True)
        if split == "development":
            rows = results[split]
            qualified = []
            for threshold in [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98, 1.0]:
                accepted = [
                    x
                    for x in rows
                    if x.get("route") != "defer" and x.get("probabilities", {}).get(x.get("route"), 0) >= threshold
                ]
                if len(accepted) >= 5 and all(x["correct"] for x in accepted):
                    qualified.append((len(accepted), threshold))
            results["threshold"] = sorted(qualified, key=lambda x: (-x[0], x[1]))[0][1] if qualified else 1.0
            save()
    print(
        "ROUTING COMPLETE",
        json.dumps(
            {
                s: {"n": len(results[s]), "correct": sum(x["correct"] for x in results[s])}
                for s in ["development", "holdout"]
            }
        ),
        flush=True,
    )
    # 后续 HTTP 与复杂任务评测在同一进程运行，避免持久化凭据。
    from evals.upgrade_http import run_http_evaluation

    await run_http_evaluation(dataset, results, save)
    print("FINAL BUDGET", json.dumps(configured_budget().snapshot()), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
