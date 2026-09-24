# Jev 决策适配层

生产请求使用异步 `async_client.decide_route`，由 `decision-router` 模式接入；默认关闭。
旧同步 `JevClient` 保留供历史评测复现，现在同样经过累计预算账本，不能绕过额度。

## 原语与策略

- Choice 返回候选、完整概率分布和 confidence；confidence 描述分布集中程度，不是正确率。
- Score 返回等级分布与加权评分；Noul 返回“是”的概率，不带相同的 confidence 字段。
- 候选来自已注册的可用服务；未知、非法概率、缺字段或超时都不能直接触发业务操作。
- `safety_gate` 的拒绝决定由 `refuse_probability` 单独与阈值比较；注入检测是独立信号，并非两者同时为真才拒绝。当前升级没有把此模型判断作为授权依据。

## 配置

使用 `TYPESAFE_API_KEY`，模型固定为 `jev-1.13.0`。请求地址为 `https://api.typesafe.ai/v1/systemone`。
价格、额度、调用次数和过期检查统一由 `shared.campaign_budget` 与 `shared.paid_transport` 管理。
所有重试都单独预留费用；旧同步客户端的显式重试也计入同一活动。

`DECISION_ROUTING_MODE` 为 off／shadow／active；阈值只能由开发样本选择，不能在留出集调优。
详细实现、费用边界与真实结果见仓库 `docs/agent-upgrade.md` 和 `docs/upgrade-verification.md`。

旧 `evals/jev_comparison/FINDINGS.zh-CN.md` 保留其历史背景，不能把其中估算的 LLM 成本或单次英文样本结果当作当前端到端收益。
