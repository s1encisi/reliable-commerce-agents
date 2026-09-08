# 售后代码导航与基线证据

[技术方案](after-sales-reliability-plan.md) · [架构导读](architecture.md)

基线：26f47c494dd6b371312593e82f066713f6f56e9c。以下为静态代码事实和待验证问题，不是已执行的漏洞复现或性能实验。

## 三条入口需要分别核对

| 路径 | 主要代码 | 关键行为 |
|---|---|---|
| 自由工具调用 | [return_tools.py](../../agents/python/shared/tools/return_tools.py) | 资格查询、发起退货、退款和状态查询 |
| 工具审批后执行 | [hitl.py](../../agents/python/shared/hitl.py) | 审批记录、领取执行权、另一套 SQL 执行分支 |
| 固定退换货工作流 | [return_replace.py](../../agents/python/workflows/return_replace.py) 与 [workflow_mode.py](../../agents/python/orchestrator/modes/workflow_mode.py) | 顺序步骤、订单解析、工作流审批与恢复 |

## 已观察到的差异

| 编号 | 代码事实 | 待复现的场景 |
|---|---|---|
| E01 | check_return_eligibility 查询签收历史，超过 30 天时拒绝 | 临界时刻的精度与统一政策口径 |
| E02 | 缺签收时间时直接设置 days_remaining=30，再返回 eligible=True | 历史缺失或时间不可信的订单 |
| E03 | initiate_return 重新检查归属、状态和重复记录，但函数内未重做同样的期限检查 | 跳过资格查询或查询后跨过期限 |
| E04 | execute_approved_action 的 initiate_return 分支单独写 SQL，默认 original_payment | 审批入口与普通入口的规则及字段是否一致 |
| E05 | _resolve_order 未找到 UUID 时查询 limit=1 并使用最新订单 | 有多个订单而用户指代不清 |
| E06 | 固定工作流包含 initiate-return → search-replacements → hitl-gate 的顺序 | 现有工作流审批究竟批准哪项动作，是否已有前置写入 |

E06 不能简化为“整个流程审批之前完全没有副作用”。工作流门控、工具门控和直接函数调用的实际组合需要通过入口级测试确认。新的方案将审批语义明确绑定到准备执行的业务操作。

## 为什么要统一规则

资格查询只表达某一时刻的可执行性。审批可能耗时，数据可能被其他请求更改，所以提交时需要重读必要状态并校验。不能把“模型之前查询过资格”当成代码层执行前提。

模型也不能选择自己的身份、批准自己的敏感动作或解释成一个数据库里不存在的订单。把这些约束放在可信 Python 执行层，再由提示词帮助用户理解结果。

## 辅助模块

| 模块 | 已有能力 | 后续核对点 |
|---|---|---|
| [tool_inputs.py](../../agents/python/shared/tool_inputs.py) | UUID、原因、退款方式等校验 | 三条入口是否共同调用 |
| [idempotency.py](../../agents/python/shared/idempotency.py) | 预留、缓存重放、陈旧请求接管 | 错误缓存、事务窗口与租约所有权 |
| [http_resilience.py](../../agents/python/shared/http_resilience.py) | 有限重试与熔断 | 有副作用请求能否安全重试 |
| [checkpoint_storage.py](../../agents/python/shared/checkpoint_storage.py) | 工作流检查点 | 恢复是否重放已提交副作用 |
| [agent_observability.py](../../agents/python/shared/agent_observability.py) | 工具步骤采集 | 错误结果与异常是否分别记录 |
| [evals/harness.py](../../agents/python/evals/harness.py) | 通过实际运行入口评估 | 增加最终业务状态断言 |

## 第一轮只复现一个案例

建议先准备一个已签收但缺少 delivered 历史的合成订单，分别调用资格查询与受控执行入口。记录返回值与数据库前后状态，核对 E02。当前没有这一轮实验结果，不提前给出“修复成功”结论。
