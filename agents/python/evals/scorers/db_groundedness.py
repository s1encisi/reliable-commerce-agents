"""确定性的、建立在第二阶段事实核验（grounding）校验器之上的事实核验评分器。

替代了旧的 ``AgentEvaluator._score_groundedness``（evaluator.py）——后者只要
调用了任何工具就返回 1.0，从不把响应内容与工具实际返回的内容作比较，因此
一个捏造的价格与一个真实的价格得分完全相同。这里的得分 =
``verified_claims / total_claims``，由 ``shared/grounding/verifier.py``
中与生产流量相同的三层校验（账本匹配 -> 批量数据库匹配 -> 一致性）计算得出。

它继承了该校验器自身已记录的局限：这里检查的是某个论断是否为真（而非捏造），
而不是它是否对该用户获得授权——事实核验与授权是两个不同的问题。
"""

from __future__ import annotations

from typing import Any

import asyncpg

from shared.grounding.extractor import extract_claims
from shared.grounding.ledger import GroundingLedger
from shared.grounding.verifier import GroundingReport, verify_claims


def score_from_report(grounding: dict[str, Any] | None) -> float:
    """基于一份已计算好的报告打分（例如 ``RunOutcome.grounding``，
    它在生产运行期间已由 ``GroundingVerificationMiddleware`` 填充——
    免费，无需额外的数据库往返）。

    当没有任何可核验的内容时返回 1.0：一个没有提出任何可检查论断的响应
    并非缺乏事实支撑，它只是没有断言任何具体内容。
    """
    if not grounding or grounding.get("total", 0) == 0:
        return 1.0
    return grounding["verified"] / grounding["total"]


async def score_groundedness(
    response_text: str,
    pool: asyncpg.Pool | None,
    ledger: GroundingLedger | None = None,
) -> tuple[float, dict[str, Any]]:
    """从零开始的事实核验检查——在尚未计算过报告时使用（例如评测环境中
    ``GROUNDING_MODE=off``，或为没有经过中间件的任意文本打分）。
    """
    claims = extract_claims(response_text)
    if claims.total_count == 0:
        return 1.0, _report_dict(GroundingReport())

    report = await verify_claims(claims, ledger, pool)
    score = report.verified_count / report.total_count if report.total_count else 1.0
    return score, _report_dict(report)


def _report_dict(report: GroundingReport) -> dict[str, Any]:
    return {
        "total": report.total_count,
        "verified": report.verified_count,
        "unverified": report.unverified_count,
        "claims": [
            {
                "type": v.claim_type,
                "id": v.identifier,
                "status": v.status,
                "detail": v.detail,
                "source": v.source,
            }
            for v in report.verdicts
        ],
    }
