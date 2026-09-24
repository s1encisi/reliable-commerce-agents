"""智能体评测框架——从事实核验（grounding）、正确性、完整性三个维度为智能体的回复打分。

加载黄金数据集，把每个输入都跑过真实的生产执行路径
（``evals/harness.py::ProductionRunner``），并生成一份带分数的汇总报告。

历史备注：它过去自己手写了一套 OpenAI 工具调用循环，并直接调用未加装饰器的
原始工具函数，绕过了真实请求会经过的每一层护栏 / 人工参与
（Human-in-the-Loop，HITL）/ 事实核验（grounding）中间件。``ProductionRunner``
修复了这个问题——参见它的模块文档字符串。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.harness import ProductionRunner
from shared.cost import estimate_cost

logger = logging.getLogger(__name__)


def _current_model() -> str:
    from shared.config import settings

    if settings.LLM_PROVIDER.lower() == "azure":
        return settings.AZURE_OPENAI_DEPLOYMENT
    return settings.LLM_MODEL


@dataclass
class EvalCase:
    """来自黄金数据集的单条评测用例。"""

    input: str
    expected_tools: list[str]
    expected_fields: list[str]
    criteria: dict[str, bool]
    # 仅编排器使用：该查询应当经由 call_specialist_agent 被路由到的专业智能体。
    # 一旦设置，正确性就按路由结果打分。
    expected_route: str | None = None


@dataclass
class EvalResult:
    """单条评测用例的评分结果。"""

    input: str
    groundedness_score: float = 0.0
    correctness_score: float = 0.0
    completeness_score: float = 0.0
    overall_score: float = 0.0
    tools_called: list[str] = field(default_factory=list)
    fields_found: list[str] = field(default_factory=list)
    fields_missing: list[str] = field(default_factory=list)
    route_called: str | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    grounding: dict[str, Any] | None = None
    judge_reasoning: str | None = None
    error: str | None = None
    fixture_missing: bool = False
    passed: bool = False


@dataclass
class EvalSummary:
    """跨所有评测用例的聚合结果。"""

    agent_name: str
    dataset_path: str
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    avg_groundedness: float = 0.0
    avg_correctness: float = 0.0
    avg_completeness: float = 0.0
    overall_score: float = 0.0
    total_latency_ms: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    estimated_cost_usd: float | None = 0.0
    results: list[EvalResult] = field(default_factory=list)

    @property
    def missing_fixtures(self) -> int:
        """因缺少回放夹具而失败的用例数。

        这与低分不同：智能体根本没有运行，因此它的质量没有任何东西被测量。
        由 ``run_evals`` 单独报告，这样一套损坏的夹具语料就不会伪装成质量回归。
        """
        return sum(1 for r in self.results if r.fixture_missing)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "dataset_path": self.dataset_path,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "avg_groundedness": round(self.avg_groundedness, 3),
            "avg_correctness": round(self.avg_correctness, 3),
            "avg_completeness": round(self.avg_completeness, 3),
            "overall_score": round(self.overall_score, 3),
            "total_latency_ms": self.total_latency_ms,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4) if self.estimated_cost_usd is not None else None,
            "missing_fixtures": self.missing_fixtures,
            "results": [
                {
                    "input": r.input,
                    "groundedness_score": round(r.groundedness_score, 3),
                    "correctness_score": round(r.correctness_score, 3),
                    "completeness_score": round(r.completeness_score, 3),
                    "overall_score": round(r.overall_score, 3),
                    "tools_called": r.tools_called,
                    "fields_found": r.fields_found,
                    "fields_missing": r.fields_missing,
                    "route_called": r.route_called,
                    "latency_ms": r.latency_ms,
                    "tokens_in": r.tokens_in,
                    "tokens_out": r.tokens_out,
                    "grounding": r.grounding,
                    "judge_reasoning": r.judge_reasoning,
                    "error": r.error,
                    "fixture_missing": r.fixture_missing,
                    "passed": r.passed,
                }
                for r in self.results
            ],
        }


def load_dataset(path: str | Path) -> list[EvalCase]:
    """从 JSON 文件加载黄金数据集。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    with open(path) as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError(f"Dataset must be a JSON array, got {type(raw).__name__}")

    cases = []
    for i, entry in enumerate(raw):
        if not all(k in entry for k in ("input", "expected_tools", "expected_fields", "criteria")):
            raise ValueError(f"Dataset entry {i} missing required fields")
        cases.append(
            EvalCase(
                input=entry["input"],
                expected_tools=entry["expected_tools"],
                expected_fields=entry["expected_fields"],
                criteria=entry["criteria"],
                expected_route=entry.get("expected_route"),
            )
        )

    return cases


class AgentEvaluator:
    """对某个智能体（经由 ``ProductionRunner``）运行评测用例并为其结果打分。

    打分维度：
      - 事实核验（groundedness）(0-1)：回复中的论断是否经得起数据库的检验
        （``shared/grounding/verifier.py``），而不只是"是否调用过某个工具"。
      - 正确性 (0-1)：是否调用了正确的工具 / 路由到了正确的专业智能体？
      - 完整性 (0-1)：回复是否覆盖了预期内容？默认使用关键词别名匹配
        （快、免费、确定性——冒烟套件的模式）；设置 ``use_llm_judge=True``
        可进行一次真实的评判调用（完整套件的模式）。
    """

    def __init__(self, agent_name: str, pass_threshold: float = 0.7, *, use_llm_judge: bool = False) -> None:
        self.agent_name = agent_name
        self.pass_threshold = pass_threshold
        self.use_llm_judge = use_llm_judge
        self._runner = ProductionRunner(agent_name)

    async def run_once(self, user_input: str) -> dict[str, Any]:
        """让单个输入走一遍生产路径（安全套件使用）。"""
        outcome = await self._runner.run(user_input)
        return {
            "text": outcome.text,
            "tokens_in": outcome.tokens_in,
            "tokens_out": outcome.tokens_out,
            "tools_called": outcome.tools_called,
            "routes": outcome.routes,
            "grounding": outcome.grounding,
            "guardrail_flags": outcome.guardrail_flags,
            "error": outcome.error,
        }

    async def evaluate_dataset(self, dataset_path: str | Path) -> EvalSummary:
        """运行数据集中的全部用例并返回聚合分数。"""
        cases = load_dataset(dataset_path)
        summary = EvalSummary(
            agent_name=self.agent_name,
            dataset_path=str(dataset_path),
            total_cases=len(cases),
        )

        for i, case in enumerate(cases):
            case_id = f"{self.agent_name}:{Path(dataset_path).stem}:{i}"
            result = await self._evaluate_case(case, case_id)
            summary.results.append(result)

            if result.passed:
                summary.passed_cases += 1
            else:
                summary.failed_cases += 1

            summary.total_latency_ms += result.latency_ms
            summary.total_tokens_in += result.tokens_in
            summary.total_tokens_out += result.tokens_out

        # 计算平均值
        n = len(summary.results)
        if n > 0:
            summary.avg_groundedness = sum(r.groundedness_score for r in summary.results) / n
            summary.avg_correctness = sum(r.correctness_score for r in summary.results) / n
            summary.avg_completeness = sum(r.completeness_score for r in summary.results) / n
            summary.overall_score = (
                summary.avg_groundedness * 0.4 + summary.avg_correctness * 0.4 + summary.avg_completeness * 0.2
            )

        summary.estimated_cost_usd = estimate_cost(_current_model(), summary.total_tokens_in, summary.total_tokens_out)

        return summary

    async def _evaluate_case(self, case: EvalCase, case_id: str) -> EvalResult:
        """针对智能体评测单条用例。"""
        result = EvalResult(input=case.input)

        start = time.monotonic()
        outcome = await self._runner.run(case.input)
        result.latency_ms = int((time.monotonic() - start) * 1000)

        if outcome.error:
            result.error = outcome.error
            result.fixture_missing = outcome.fixture_missing
            logger.error("Eval case failed: %s — %s", case.input[:60], outcome.error)
            return result

        response_text = outcome.text
        result.tools_called = outcome.tools_called
        result.tokens_in = outcome.tokens_in
        result.tokens_out = outcome.tokens_out
        result.grounding = outcome.grounding

        # 事实核验（grounding）：在可用时复用 GroundingVerificationMiddleware
        # 在生产运行期间已经算出的报告（免费）；否则回退到从零开始的数据库
        # 校验（例如 GROUNDING_MODE=off）。
        if outcome.grounding is not None:
            from evals.scorers.db_groundedness import score_from_report

            result.groundedness_score = score_from_report(outcome.grounding)
        else:
            result.groundedness_score = await self._score_groundedness_fallback(response_text, case.criteria)

        # 正确性：是否调用了预期的工具？
        result.correctness_score = self._score_correctness(outcome.tools_called, case.expected_tools)

        # 路由覆盖：对于编排器用例，有意义的信号是它是否把处理权交接给了
        # *正确* 的专业智能体，而不仅仅是否调用了路由工具。
        if case.expected_route:
            result.route_called = outcome.routes[0] if outcome.routes else None
            result.correctness_score = self._score_routing(outcome.routes, case.expected_route)

        # 完整性
        if self.use_llm_judge:
            from evals.scorers.llm_judge import judge_response

            verdict = await judge_response(case_id, case.input, response_text, case.expected_fields)
            result.completeness_score = verdict.score
            result.judge_reasoning = verdict.reasoning
            result.fields_found = list(case.expected_fields) if verdict.score >= 1.0 else []
            result.fields_missing = [] if verdict.score >= 1.0 else list(case.expected_fields)
        else:
            result.completeness_score, result.fields_found, result.fields_missing = self._score_completeness_keyword(
                response_text, case.expected_fields
            )

        # 加权总分
        result.overall_score = (
            result.groundedness_score * 0.4 + result.correctness_score * 0.4 + result.completeness_score * 0.2
        )
        result.passed = result.overall_score >= self.pass_threshold

        return result

    @staticmethod
    async def _score_groundedness_fallback(response_text: str, criteria: dict[str, bool]) -> float:
        """没有可用的生产环境事实核验（grounding）报告（GROUNDING_MODE=off）时——
        尝试从零开始做一次数据库校验；如果连数据库连接池也未初始化，
        则回退到旧的"是否本来就预期要做事实核验"的启发式判断，
        而不是让一个从未要求数据库访问的套件崩溃。
        """
        expects_grounded = criteria.get("grounded", True)
        if not expects_grounded:
            return 1.0

        try:
            from shared.db import get_pool

            pool = get_pool()
        except RuntimeError:
            pool = None

        from evals.scorers.db_groundedness import score_groundedness

        score, _ = await score_groundedness(response_text, pool)
        return score

    @staticmethod
    def _score_correctness(tools_called: list[str], expected_tools: list[str]) -> float:
        """为是否调用了正确的工具打分。

        部分得分：若 3 个预期工具中调用了 2 个，则得分 = 0.67。
        加分项：调用额外的有用工具不扣分。
        """
        if not expected_tools:
            return 1.0  # 没有工具方面的预期

        matched = sum(1 for t in expected_tools if t in tools_called)
        return matched / len(expected_tools)

    @staticmethod
    def _score_routing(routes: list[str], expected_route: str) -> float:
        """为编排器的处理权交接打分：1.0 为正确的专业智能体，0.5 为错误，0.0 为未交接。"""
        if not routes:
            return 0.0
        return 1.0 if expected_route in routes else 0.5

    @staticmethod
    def _score_completeness_keyword(
        response_text: str, expected_fields: list[str]
    ) -> tuple[float, list[str], list[str]]:
        """通过字段名别名进行快速、免费、确定性的完整性检查。

        比 LLM 评判器更粗糙（一个孤零零的 "$" 也算满足 "price" 字段），
        但零成本且与回放兼容——这是冒烟套件的模式。
        """
        if not expected_fields:
            return 1.0, [], []

        response_lower = response_text.lower()
        found: list[str] = []
        missing: list[str] = []

        field_aliases: dict[str, list[str]] = {
            "name": ["name", "product", "title"],
            "price": ["price", "$", "cost", "usd"],
            "rating": ["rating", "stars", "score", "rated"],
            "description": ["description", "about", "details"],
            "category": ["category", "type", "department"],
            "specs": ["specs", "specifications", "features"],
            "status": ["status", "state", "condition"],
            "order_id": ["order", "order_id", "#"],
            "tracking_number": ["tracking", "shipment", "carrier"],
            "items": ["items", "products", "line items"],
            "total": ["total", "amount", "sum"],
            "created_at": ["date", "created", "placed", "ordered"],
            "new_status": ["cancelled", "canceled", "new status"],
            "refund_amount": ["refund", "money back", "credit"],
            "return_eligible": ["eligible", "return", "returnable"],
        }

        for fld in expected_fields:
            aliases = field_aliases.get(fld, [fld])
            if any(alias in response_lower for alias in aliases):
                found.append(fld)
            else:
                missing.append(fld)

        score = len(found) / len(expected_fields) if expected_fields else 1.0
        return score, found, missing


def format_summary_report(summary: EvalSummary, verbose: bool = False) -> str:
    """把 EvalSummary 格式化为人类可读的报告。"""
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 70)
    lines.append(f"  EVALUATION REPORT: {summary.agent_name}")
    lines.append("=" * 70)
    lines.append(f"  Dataset:     {summary.dataset_path}")
    lines.append(f"  Total cases: {summary.total_cases}")
    lines.append(f"  Passed:      {summary.passed_cases}")
    lines.append(f"  Failed:      {summary.failed_cases}")
    lines.append("-" * 70)
    lines.append(f"  Groundedness:  {summary.avg_groundedness:.1%}")
    lines.append(f"  Correctness:   {summary.avg_correctness:.1%}")
    lines.append(f"  Completeness:  {summary.avg_completeness:.1%}")
    lines.append(f"  Overall Score: {summary.overall_score:.1%}")
    lines.append("-" * 70)
    lines.append(f"  Total latency: {summary.total_latency_ms:,}ms")
    lines.append(f"  Tokens (in):   {summary.total_tokens_in:,}")
    lines.append(f"  Tokens (out):  {summary.total_tokens_out:,}")
    cost = f"${summary.estimated_cost_usd:.4f}" if summary.estimated_cost_usd is not None else "未计价（查看本币账本）"
    lines.append(f"  Est. cost:     {cost}")
    lines.append("=" * 70)

    if verbose:
        lines.append("")
        for i, r in enumerate(summary.results, 1):
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"  [{status}] Case {i}: {r.input[:60]}")
            lines.append(
                f"    Groundedness: {r.groundedness_score:.1%}  "
                f"Correctness: {r.correctness_score:.1%}  "
                f"Completeness: {r.completeness_score:.1%}  "
                f"Overall: {r.overall_score:.1%}"
            )
            lines.append(f"    Tools called: {', '.join(r.tools_called) or '(none)'}")
            if r.fields_missing:
                lines.append(f"    Missing fields: {', '.join(r.fields_missing)}")
            if r.judge_reasoning:
                lines.append(f"    Judge: {r.judge_reasoning}")
            if r.error:
                lines.append(f"    Error: {r.error}")
            lines.append(f"    Latency: {r.latency_ms}ms")
            lines.append("")

    return "\n".join(lines)
