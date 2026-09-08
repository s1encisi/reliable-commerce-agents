# 安全与可靠性：已有机制与适用边界

[上游安全文档](../security-guide.md) · [售后代码导航](after-sales-code-map.md)

## 已有能力

| 机制 | 代码 | 作用与边界 |
|---|---|---|
| 角色与归属检查 | shared/guardrails/roles.py、各工具 SQL | 后端验证权限，不能只依赖提示词 |
| 工具参数校验 | shared/tool_inputs.py | 参数形状正确不代表业务允许执行 |
| 人工审批 | shared/hitl.py、workflows/return_replace.py | 需要区分批准的对象、参数和生效时点 |
| 幂等 | shared/idempotency.py | 避免同一动作重复处理；仍需验证崩溃窗口 |
| 网络重试与熔断 | shared/http_resilience.py | 缓解暂时网络故障；不证明重复执行写操作安全 |
| 速率限制 | shared/rate_limit.py | 限制请求频率；Redis 不可用时采用放行策略 |
| 注入检测与输出清洗 | shared/guardrails | 识别或处理部分危险文本；不是完整安全证明 |
| 事实核验 | shared/grounding | 检查可提取的事实声明；覆盖范围受提取器限制 |
| 成本预算 | shared/guardrails/cost_budget_middleware.py | 单次模型响应后累计，再决定是否允许下一轮 |

以上相对路径均以 agents/python 为根。

## 当前默认配置

依据 [shared/config.py](../../agents/python/shared/config.py) 的声明：

| 配置 | 默认值 |
|---|---|
| RATE_LIMIT_ENABLED | true |
| GUARDRAILS_BLOCK_ON_INJECTION | false |
| GROUNDING_MODE | annotate |
| COST_BUDGET_MODE | observe |
| COST_BUDGET_USD_PER_RUN | None |

默认值不等于本地 .env 的实际生效值。本轮没有读取密钥配置，也没有验证正在运行的环境。

observe 表示观察记录；annotate 表示附加核验信息；enforce 表示尝试执行限制。不能因为代码里有 enforce 就声称当前所有请求都已强制拦截。

## 四个实际边界

**流式事实核验。** 当前核验钩子处理完成后的响应。先前发出的片段无法撤回；严格场景需要先校验关键事实再呈现，或先使用非流式路径。

**结果未知。** 客户端超时可能发生在服务端提交之后。应先核查业务操作状态，不能把超时直接等同于没有执行。

**审批后状态变化。** 审批不是永久通行证。订单、政策或参数变化后，应重新校验或重新审批。

**不可信文本。** 商品描述、评价、检索文档和工具返回文字只能作为数据，不应授予权限或扩大动作范围。注入检测只能辅助；关键动作仍要通过确定性业务检查。

## 个人改进范围

技术方案优先统一退货业务条件、审批与执行入口，增加按错误类型处理的恢复策略和故障测试。模型训练、新支付接口、跨企业系统和全面安全认证不属于本轮文档工作。
