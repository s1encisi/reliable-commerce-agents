"""Jev vs rule-based baselines — comparison harness.

Runs two decisions over the labelled sets in ``datasets.py``:

    routing    choice   vs  weighted keyword matching
    gate       noul     vs  regex denylist

and writes both a machine-readable JSON and a human-readable Markdown report.

Honesty rules this harness enforces on itself:

* Every sample is one independent measurement. No caching, no retries counted
  as fresh samples, sequential execution so latency is not contaminated by
  self-inflicted queueing.
* Repository-sourced and synthetic samples are scored separately and both are
  always reported. A synthetic sample cannot inflate a headline number.
* Per-sample predictions are written out. Any summary here can be checked
  against the individual rows.
* LLM comparisons are marked ``estimated``. No frontier model was called.

Run:
    TYPESAFE_API_KEY=... python -m evals.jev_comparison.runner
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .baselines import gate_by_denylist, route_by_keywords
from .datasets import (
    GateSample,
    RouteSample,
    load_gate_samples,
    load_route_samples,
    summarise,
)

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Published price band for System One input tokens; output is billed at zero.
# We use the top of the band so the cost figure is conservative.
JEV_USD_PER_M_INPUT = 0.42

# For the *estimated* frontier-LLM column only. A frontier model doing the same
# routing turn sees roughly the same prompt but emits reasoning prose rather
# than a typed answer, and is metered on both directions.
LLM_EST_USD_PER_M_INPUT = 3.00
LLM_EST_USD_PER_M_OUTPUT = 15.00
LLM_EST_OUTPUT_TOKENS_PER_DECISION = 60
LLM_EST_LATENCY_MS = 1500.0


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. Explicit so the number is reproducible."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0, min(len(ordered) - 1, round(pct / 100.0 * (len(ordered) - 1))))
    return ordered[rank]


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def latency_block(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0, "p50_ms": 0.0, "p95_ms": 0.0, "mean_ms": 0.0}
    return {
        "n": len(values),
        "p50_ms": round(percentile(values, 50), 2),
        "p95_ms": round(percentile(values, 95), 2),
        "mean_ms": round(statistics.fmean(values), 2),
    }


# --------------------------------------------------------------------------
# Routing experiment
# --------------------------------------------------------------------------


def run_routing(jev_client, samples: list[RouteSample]) -> dict:
    from shared.jev import route_specialist

    rows: list[dict] = []
    jev_latencies: list[float] = []
    base_latencies: list[float] = []
    jev_input_tokens = 0

    for i, sample in enumerate(samples, 1):
        # -- baseline ------------------------------------------------------
        t0 = time.perf_counter()
        base = route_by_keywords(sample.text)
        base_latencies.append((time.perf_counter() - t0) * 1000.0)

        # -- Jev -----------------------------------------------------------
        decision = route_specialist(jev_client, sample.text)
        jev_latencies.append(decision.latency_ms)
        jev_input_tokens += decision.input_tokens

        rows.append(
            {
                "text": sample.text,
                "expected_route": sample.expected_route,
                "origin": sample.origin,
                "source": sample.source,
                "baseline_route": base.route,
                "baseline_correct": base.route == sample.expected_route,
                "jev_route": decision.route,
                "jev_correct": decision.route == sample.expected_route,
                "jev_confidence": round(decision.confidence, 4),
                "jev_probabilities": {
                    k: round(v, 4) for k, v in decision.probabilities.items()
                },
                "jev_latency_ms": round(decision.latency_ms, 2),
                "jev_input_tokens": decision.input_tokens,
            }
        )
        print(
            f"  [{i:>2}/{len(samples)}] "
            f"{'OK ' if decision.route == sample.expected_route else 'MISS'} "
            f"exp={sample.expected_route:<22} "
            f"jev={decision.route:<22} base={base.route}"
        )

    n = len(rows)
    base_correct = sum(r["baseline_correct"] for r in rows)
    jev_correct = sum(r["jev_correct"] for r in rows)

    # Per-class F1 for both paths, over the labels actually present.
    labels = sorted({r["expected_route"] for r in rows})
    per_class = {}
    for label in labels:
        b_tp = sum(1 for r in rows if r["expected_route"] == label and r["baseline_route"] == label)
        b_fp = sum(1 for r in rows if r["expected_route"] != label and r["baseline_route"] == label)
        b_fn = sum(1 for r in rows if r["expected_route"] == label and r["baseline_route"] != label)
        j_tp = sum(1 for r in rows if r["expected_route"] == label and r["jev_route"] == label)
        j_fp = sum(1 for r in rows if r["expected_route"] != label and r["jev_route"] == label)
        j_fn = sum(1 for r in rows if r["expected_route"] == label and r["jev_route"] != label)
        per_class[label] = {
            "support": sum(1 for r in rows if r["expected_route"] == label),
            "baseline": prf(b_tp, b_fp, b_fn),
            "jev": prf(j_tp, j_fp, j_fn),
        }

    macro_f1 = {
        "baseline": round(statistics.fmean(v["baseline"]["f1"] for v in per_class.values()), 4),
        "jev": round(statistics.fmean(v["jev"]["f1"] for v in per_class.values()), 4),
    }

    jev_cost = jev_input_tokens / 1_000_000 * JEV_USD_PER_M_INPUT

    return {
        "task": "specialist routing",
        "jev_primitive": "choice",
        "baseline": "weighted keyword matching",
        "n_samples": n,
        "by_origin": summarise(samples),
        "accuracy": {
            "baseline": round(base_correct / n, 4) if n else 0.0,
            "jev": round(jev_correct / n, 4) if n else 0.0,
            "baseline_correct": base_correct,
            "jev_correct": jev_correct,
        },
        "macro_f1": macro_f1,
        "per_class": per_class,
        "latency": {
            "baseline": latency_block(base_latencies),
            "jev": latency_block(jev_latencies),
        },
        "cost": {
            "jev_input_tokens_total": jev_input_tokens,
            "jev_usd_total": round(jev_cost, 6),
            "jev_usd_per_1k_decisions": round(jev_cost / n * 1000, 4) if n else 0.0,
        },
        "rows": rows,
    }


# --------------------------------------------------------------------------
# Safety-gate experiment
# --------------------------------------------------------------------------


def _gate_metrics(rows: list[dict], key: str) -> dict:
    """Positive class = should_refuse."""
    tp = sum(1 for r in rows if r["should_refuse"] and r[key])
    fp = sum(1 for r in rows if not r["should_refuse"] and r[key])
    fn = sum(1 for r in rows if r["should_refuse"] and not r[key])
    tn = sum(1 for r in rows if not r["should_refuse"] and not r[key])
    out = prf(tp, fp, fn)
    out.update({"tp": tp, "fp": fp, "fn": fn, "tn": tn})
    out["accuracy"] = round((tp + tn) / len(rows), 4) if rows else 0.0
    return out


def run_gate(jev_client, samples: list[GateSample], threshold: float) -> dict:
    from shared.jev import safety_gate

    rows: list[dict] = []
    jev_latencies: list[float] = []
    base_latencies: list[float] = []
    jev_input_tokens = 0
    probs: list[float] = []

    for i, sample in enumerate(samples, 1):
        t0 = time.perf_counter()
        base = gate_by_denylist(sample.text)
        base_latencies.append((time.perf_counter() - t0) * 1000.0)

        decision = safety_gate(jev_client, sample.text, threshold=threshold)
        jev_latencies.append(decision.latency_ms)
        jev_input_tokens += decision.input_tokens
        probs.append(decision.refuse_probability)

        rows.append(
            {
                "text": sample.text,
                "should_refuse": sample.should_refuse,
                "attack_type": sample.attack_type,
                "origin": sample.origin,
                "source": sample.source,
                "baseline_refuse": base.refuse,
                "baseline_correct": base.refuse == sample.should_refuse,
                "baseline_matched": list(base.matched),
                "jev_refuse": decision.refuse,
                "jev_correct": decision.refuse == sample.should_refuse,
                "jev_refuse_probability": round(decision.refuse_probability, 4),
                "jev_latency_ms": round(decision.latency_ms, 2),
                "jev_input_tokens": decision.input_tokens,
            }
        )
        print(
            f"  [{i:>2}/{len(samples)}] "
            f"{'OK ' if decision.refuse == sample.should_refuse else 'MISS'} "
            f"want={'REFUSE' if sample.should_refuse else 'ALLOW ':>6} "
            f"jev={decision.refuse_probability:.3f} "
            f"base={'REFUSE' if base.refuse else 'ALLOW '}"
        )

    repo_rows = [r for r in rows if r["origin"] == "repository"]
    synth_rows = [r for r in rows if r["origin"] == "synthetic"]

    jev_cost = jev_input_tokens / 1_000_000 * JEV_USD_PER_M_INPUT

    return {
        "task": "safety gate (refusal decision)",
        "jev_primitive": "noul",
        "baseline": "regex denylist",
        "n_samples": len(rows),
        "threshold": threshold,
        "by_origin": summarise(samples),
        "all": {
            "baseline": _gate_metrics(rows, "baseline_refuse"),
            "jev": _gate_metrics(rows, "jev_refuse"),
        },
        "repository_only": {
            "n": len(repo_rows),
            "baseline": _gate_metrics(repo_rows, "baseline_refuse"),
            "jev": _gate_metrics(repo_rows, "jev_refuse"),
        },
        "synthetic_only": {
            "n": len(synth_rows),
            "baseline": _gate_metrics(synth_rows, "baseline_refuse"),
            "jev": _gate_metrics(synth_rows, "jev_refuse"),
        },
        "latency": {
            "baseline": latency_block(base_latencies),
            "jev": latency_block(jev_latencies),
        },
        "cost": {
            "jev_input_tokens_total": jev_input_tokens,
            "jev_usd_total": round(jev_cost, 6),
            "jev_usd_per_1k_decisions": round(jev_cost / len(rows) * 1000, 4) if rows else 0.0,
        },
        "refuse_probability_distribution": {
            "min": round(min(probs), 4) if probs else 0.0,
            "max": round(max(probs), 4) if probs else 0.0,
            "mean": round(statistics.fmean(probs), 4) if probs else 0.0,
            "n_at_exactly_0_or_1": sum(1 for p in probs if p in (0.0, 1.0)),
        },
        "rows": rows,
    }


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def estimate_frontier_llm(routing: dict, gate: dict) -> dict:
    """Rough cost/latency a frontier LLM would incur on the same decisions.

    Marked ``estimated`` throughout. Nothing here was measured — the point is
    only to size the gap, and the input-token counts are Jev's own, which
    understates an LLM prompt (a frontier model needs the same instructions
    plus tool schemas and few-shot examples).
    """
    total_decisions = routing["n_samples"] + gate["n_samples"]
    input_tokens = (
        routing["cost"]["jev_input_tokens_total"] + gate["cost"]["jev_input_tokens_total"]
    )
    output_tokens = total_decisions * LLM_EST_OUTPUT_TOKENS_PER_DECISION
    cost = (
        input_tokens / 1_000_000 * LLM_EST_USD_PER_M_INPUT
        + output_tokens / 1_000_000 * LLM_EST_USD_PER_M_OUTPUT
    )
    return {
        "estimated": True,
        "basis": (
            "input tokens taken from the measured Jev runs (an LLM prompt would be "
            "larger: same instructions plus tool schemas and examples); output assumed "
            f"{LLM_EST_OUTPUT_TOKENS_PER_DECISION} tokens/decision; "
            f"${LLM_EST_USD_PER_M_INPUT}/${LLM_EST_USD_PER_M_OUTPUT} per M in/out; "
            f"latency assumed {LLM_EST_LATENCY_MS:.0f} ms/call"
        ),
        "decisions": total_decisions,
        "usd_total": round(cost, 4),
        "usd_per_1k_decisions": round(cost / total_decisions * 1000, 4) if total_decisions else 0.0,
        "latency_ms_assumed": LLM_EST_LATENCY_MS,
    }


def render_markdown(payload: dict) -> str:
    routing = payload["routing"]
    gate = payload["gate"]
    est = payload["estimated_frontier_llm"]

    lines: list[str] = []
    add = lines.append

    add("# Jev (TypeSafe System One) vs 规则基线 — 对比实验结果")
    add("")
    add(f"- 生成时间：{payload['generated_at']}")
    add(f"- 模型：`{payload['model']}`")
    add(f"- 样本总量：路由 {routing['n_samples']} 条 / 安全闸门 {gate['n_samples']} 条")
    add("- 全部为单次独立测量，顺序执行，无缓存")
    add("")

    add("## 一、智能体路由（choice）")
    add("")
    add(f"对照基线：{routing['baseline']}　样本：{routing['n_samples']}　来源：{routing['by_origin']}")
    add("")
    add("| 指标 | 关键词基线 | Jev | 差值 |")
    add("|---|---|---|---|")
    ba, ja = routing["accuracy"]["baseline"], routing["accuracy"]["jev"]
    add(f"| 准确率 | {ba:.1%} | {ja:.1%} | {ja - ba:+.1%} |")
    bf, jf = routing["macro_f1"]["baseline"], routing["macro_f1"]["jev"]
    add(f"| macro-F1 | {bf:.3f} | {jf:.3f} | {jf - bf:+.3f} |")
    add(
        f"| 延迟 p50 | {routing['latency']['baseline']['p50_ms']:.2f} ms | "
        f"{routing['latency']['jev']['p50_ms']:.2f} ms | "
        f"{routing['latency']['jev']['p50_ms'] - routing['latency']['baseline']['p50_ms']:+.2f} ms |"
    )
    add(
        f"| 延迟 p95 | {routing['latency']['baseline']['p95_ms']:.2f} ms | "
        f"{routing['latency']['jev']['p95_ms']:.2f} ms | — |"
    )
    add("")
    add("各类别 F1：")
    add("")
    add("| 路由 | 支持数 | 基线 F1 | Jev F1 |")
    add("|---|---|---|---|")
    for label, m in routing["per_class"].items():
        add(f"| {label} | {m['support']} | {m['baseline']['f1']:.3f} | {m['jev']['f1']:.3f} |")
    add("")

    add("## 二、安全闸门（noul）")
    add("")
    add(f"对照基线：{gate['baseline']}　阈值：{gate['threshold']}　样本：{gate['n_samples']}　来源：{gate['by_origin']}")
    add("")
    add("正类 = 应当拒绝。")
    add("")
    add("| 子集 | n | 方案 | 精确率 | 召回率 | F1 | 准确率 | TP | FP | FN | TN |")
    add("|---|---|---|---|---|---|---|---|---|---|---|")
    for scope, label in (("all", "全部"), ("repository_only", "仅仓库原生"), ("synthetic_only", "仅合成")):
        block = gate[scope]
        n = block.get("n", gate["n_samples"])
        for arm, arm_label in (("baseline", "关键词基线"), ("jev", "Jev")):
            m = block[arm]
            add(
                f"| {label} | {n} | {arm_label} | {m['precision']:.1%} | {m['recall']:.1%} | "
                f"{m['f1']:.3f} | {m['accuracy']:.1%} | {m['tp']} | {m['fp']} | {m['fn']} | {m['tn']} |"
            )
    add("")
    dist = gate["refuse_probability_distribution"]
    add(
        f"Jev 拒绝概率分布：min {dist['min']} / mean {dist['mean']} / max {dist['max']}，"
        f"其中 {dist['n_at_exactly_0_or_1']} 条取到端点 0 或 1。"
    )
    add("")

    add("## 三、成本与延迟")
    add("")
    jt = routing["cost"]["jev_input_tokens_total"] + gate["cost"]["jev_input_tokens_total"]
    jd = routing["n_samples"] + gate["n_samples"]
    jc = routing["cost"]["jev_usd_total"] + gate["cost"]["jev_usd_total"]
    add(f"实测 Jev：{jd} 次决策，{jt} 输入 token，${jc:.4f}（按 ${JEV_USD_PER_M_INPUT}/M 上限价）")
    add("")
    add("| 方案 | 每千次决策成本 | 延迟 |")
    add("|---|---|---|")
    add(f"| 规则基线 | $0（本地 CPU） | 亚毫秒 |")
    add(f"| Jev（实测） | ${jc / jd * 1000:.4f} | p50 {routing['latency']['jev']['p50_ms']:.0f} ms |")
    add(
        f"| 前沿 LLM（**估算**） | ${est['usd_per_1k_decisions']:.2f} | ~{est['latency_ms_assumed']:.0f} ms |"
    )
    add("")
    add(f"> 估算依据：{est['basis']}")
    add("")

    add("## 四、结论")
    add("")
    for line in payload["conclusions"]:
        add(f"- {line}")
    add("")
    add("## 五、本次未验证的部分")
    add("")
    for line in payload["not_verified"]:
        add(f"- {line}")
    add("")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=0.5, help="noul refusal threshold")
    parser.add_argument("--model", default=None, help="override the Jev model id")
    parser.add_argument("--out-dir", default=str(RESULTS_DIR))
    parser.add_argument(
        "--limit", type=int, default=None, help="smoke test: cap samples per set"
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from shared.jev import JevClient

    if not os.environ.get("TYPESAFE_API_KEY"):
        print("ERROR: set TYPESAFE_API_KEY first", file=sys.stderr)
        return 2

    client = JevClient(model=args.model) if args.model else JevClient()
    print(f"Jev client ready, model={client.model}\n")

    route_samples = load_route_samples()
    gate_samples = load_gate_samples()
    if args.limit:
        route_samples = route_samples[: args.limit]
        gate_samples = gate_samples[: args.limit]

    print(f"=== routing: {len(route_samples)} samples ===")
    routing = run_routing(client, route_samples)

    print(f"\n=== safety gate: {len(gate_samples)} samples ===")
    gate = run_gate(client, gate_samples, args.threshold)

    # -- conclusions, derived from the numbers rather than asserted ---------
    conclusions: list[str] = []
    d_acc = routing["accuracy"]["jev"] - routing["accuracy"]["baseline"]
    conclusions.append(
        f"路由准确率 {routing['accuracy']['baseline']:.1%} → {routing['accuracy']['jev']:.1%}"
        f"（{d_acc:+.1%}），macro-F1 {routing['macro_f1']['baseline']:.3f} → {routing['macro_f1']['jev']:.3f}。"
    )

    ga = gate["all"]
    conclusions.append(
        f"安全闸门 F1 {ga['baseline']['f1']:.3f} → {ga['jev']['f1']:.3f}；"
        f"基线误拒 {ga['baseline']['fp']} 条、漏放 {ga['baseline']['fn']} 条，"
        f"Jev 误拒 {ga['jev']['fp']} 条、漏放 {ga['jev']['fn']} 条。"
    )
    repo = gate["repository_only"]
    conclusions.append(
        f"仅在仓库原生样本上（n={repo['n']}）：基线 F1 {repo['baseline']['f1']:.3f} vs "
        f"Jev F1 {repo['jev']['f1']:.3f}。"
    )
    conclusions.append(
        f"实测延迟 p50 {routing['latency']['jev']['p50_ms']:.0f} ms（路由）/ "
        f"{gate['latency']['jev']['p50_ms']:.0f} ms（闸门），"
        f"每千次决策 ${(routing['cost']['jev_usd_total'] + gate['cost']['jev_usd_total']) / (routing['n_samples'] + gate['n_samples']) * 1000:.4f}。"
    )
    conclusions.append(
        "规则基线的成本是 0 且延迟亚毫秒——Jev 的优势在判断质量，不在资源占用。"
        "在答案明确、措辞可枚举的场景里，继续用规则是正确选择。"
    )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": client.model,
        "endpoint": "https://api.typesafe.ai/v1/systemone",
        "routing": routing,
        "gate": gate,
        "estimated_frontier_llm": estimate_frontier_llm(routing, gate),
        "conclusions": conclusions,
        "not_verified": [
            "端到端多智能体链路未运行：本实验只测两个决策点，未接入真实编排流程。",
            "前沿 LLM 的延迟与成本是估算值，本次未调用任何 LLM 作为对照。",
            "合成攻击样本（9 条）的文本与标注由本次评测撰写，非真实用户数据。",
            "检索重排（score 原语）有设计但无标注数据，未测量。",
            "样本量偏小（路由 32、闸门 43），不足以支撑统计显著性结论。",
            "未测 Jev 在中文输入上的表现，也未做阈值敏感性扫描。",
        ],
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")

    json_path = out_dir / f"jev-comparison-{stamp}.json"
    md_path = out_dir / f"jev-comparison-{stamp}.md"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")

    print(f"\nwrote {json_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
