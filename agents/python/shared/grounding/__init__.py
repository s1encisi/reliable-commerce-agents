"""服务端事实核验，根据真实数据检查最终回答。

ledger 记录本轮工具事实，extractor 提取商品、订单卡片及正文声明，
verifier 先查台账再查数据库，middleware 按 GROUNDING_MODE 接入。
"""

from __future__ import annotations
