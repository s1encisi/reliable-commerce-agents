"""存储的评测分数基线与回归检测。

基线是某个 ``EvalSummary`` 关键分数的快照，提交到
``evals/baselines/<suite>.json``。``--baseline`` 会把一次新的运行结果与它
对比，若任何被跟踪的分数下降超过 ``--max-regression`` 则判定该次运行失败；
``--update-baseline`` 会在一次经过审慎评审的变更之后覆盖已存储的文件
（绝不会自动覆盖）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evals.evaluator import EvalSummary

_TRACKED_SCORES = ("avg_groundedness", "avg_correctness", "avg_completeness", "overall_score")


def load_baseline(path: str | Path) -> dict[str, float] | None:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def write_baseline(path: str | Path, summary: EvalSummary) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    snapshot: dict[str, float | str] = {key: round(getattr(summary, key), 4) for key in _TRACKED_SCORES}
    snapshot["agent_name"] = summary.agent_name
    snapshot["dataset_path"] = summary.dataset_path
    p.write_text(json.dumps(snapshot, indent=2) + "\n")


def check_regression(baseline: dict[str, float], summary: EvalSummary, max_regression: float) -> tuple[bool, str]:
    """返回 ``(是否回归, 人类可读的消息)``。

    只有当分数下降幅度超过 ``max_regression`` 时才判定为回归——运行之间
    的微小噪声（采样到了不同的回复、一次不稳定的评判调用）属于预期之内，
    不应导致构建失败。
    """
    lines = ["Baseline comparison:"]
    regressed = False
    for key in _TRACKED_SCORES:
        old = baseline.get(key)
        if old is None:
            continue
        new = getattr(summary, key)
        delta = new - old
        flag = ""
        if delta < -max_regression:
            regressed = True
            flag = "  ** REGRESSION **"
        lines.append(f"  {key}: {old:.3f} -> {new:.3f} ({delta:+.3f}){flag}")
    return regressed, "\n".join(lines)
