"""未计价必须保留为缺失，不能破坏原有报告输出。"""

from evals.evaluator import EvalSummary, format_summary_report


def test_unpriced_summary_serializes_null_and_displays_missing():
    summary = EvalSummary(agent_name="synthetic", dataset_path="synthetic", estimated_cost_usd=None)
    assert summary.to_dict()["estimated_cost_usd"] is None
    assert "未计价" in format_summary_report(summary)
