"""评测评分器。

- ``db_groundedness``——确定性的，复用 ``shared/grounding/verifier.py``。
  零 LLM 成本，对 CI 安全。当 ``RunOutcome.grounding`` 报告已经可用时
  （生产中间件已经算好了），优先使用 ``score_from_report()``；需要从零
  检查时回退到 ``score_groundedness()``。
- ``llm_judge``——用于相关性 / 完整性，即无法针对数据库做机械检查的那部分。
  不用于事实核验（grounding）——只要存在可供核验的真实数据，
  确定性检查就严格更优。
"""

from __future__ import annotations
