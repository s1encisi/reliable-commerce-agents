"""评测黄金数据集的模式与完整性测试。

确认每个 expected_tools 都是目标智能体的真实工具，在模型评测前
捕获拼写或结构错误。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from evals.evaluator import load_dataset

DATASETS_DIR = Path(__file__).resolve().parent.parent / "evals" / "datasets"

# 数据集文件名映射到模块与工具列表属性。
_AGENT_TOOLSETS: dict[str, tuple[str, str]] = {
    "product_discovery": ("product_discovery.agent", "AGENT_TOOLS"),
    "order_management": ("order_management.agent", "AGENT_TOOLS"),
    "pricing_promotions": ("pricing_promotions.agent", "AGENT_TOOLS"),
    "review_sentiment": ("review_sentiment.agent", "AGENT_TOOLS"),
    "inventory_fulfillment": ("inventory_fulfillment.agent", "AGENT_TOOLS"),
}


# red_team.json 使用独立安全数据模式，
# 由 test_eval_safety.py 校验，不套用普通数据集规则。
_SAFETY_DATASETS = {"red_team"}


def _all_datasets() -> list[Path]:
    return sorted(p for p in DATASETS_DIR.glob("*.json") if p.stem not in _SAFETY_DATASETS)


def _tool_names(module: str, attr: str) -> set[str]:
    mod = importlib.import_module(module)
    tools = getattr(mod, attr)
    names: set[str] = set()
    for t in tools:
        names.add(getattr(t, "name", None) or getattr(t, "__name__", str(t)))
    return names


def test_datasets_exist() -> None:
    found = {p.stem for p in _all_datasets()}
    # 五个专业智能体的数据集必须齐全。
    assert _AGENT_TOOLSETS.keys() <= found, f"missing datasets: {_AGENT_TOOLSETS.keys() - found}"


@pytest.mark.parametrize("path", _all_datasets(), ids=lambda p: p.stem)
def test_dataset_loads_and_has_cases(path: Path) -> None:
    cases = load_dataset(path)  # 违反数据模式时抛错。
    assert cases, f"{path.name} has no cases"
    for case in cases:
        assert isinstance(case.input, str) and case.input.strip()
        assert isinstance(case.expected_tools, list)
        assert isinstance(case.expected_fields, list)
        assert isinstance(case.criteria, dict)


@pytest.mark.parametrize("path", _all_datasets(), ids=lambda p: p.stem)
def test_no_duplicate_inputs(path: Path) -> None:
    raw = json.loads(path.read_text())
    inputs = [c["input"] for c in raw]
    assert len(inputs) == len(set(inputs)), f"{path.name} has duplicate inputs"


@pytest.mark.parametrize("stem", sorted(_AGENT_TOOLSETS), ids=lambda s: s)
def test_expected_tools_exist_on_agent(stem: str) -> None:
    path = DATASETS_DIR / f"{stem}.json"
    if not path.exists():
        pytest.skip(f"{stem}.json not present")
    valid = _tool_names(*_AGENT_TOOLSETS[stem])
    cases = load_dataset(path)
    for case in cases:
        for tool in case.expected_tools:
            assert tool in valid, f"{stem}.json references unknown tool '{tool}' (valid: {sorted(valid)})"
