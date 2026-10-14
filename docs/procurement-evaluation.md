# 采购运行时评测

采购评测验证结构化需求确认后的只读执行、依赖调度、局部修复和证据核验。当前评测使用真实隔离 PostgreSQL、真实 MAF 运行器和真实电商 SQL 工具；模型调用为零。它不测开放中文需求理解、模型规划质量、生产转化率或 GMV。

## 本轮结果

2026-10-14 首轮执行了 52 项采购目标测试和 120 条正式 trial；随后模型工具错误语义修复改变 service.py，生成独立 [R2 结果](evaluation/procurement-20261014-r2.md) 和 [R2 原始 JSON](evaluation/procurement-20261014-r2.json) 重新绑定交付源码。[首轮说明](evaluation/procurement-20261014.md) 与 [首轮 JSON](evaluation/procurement-20261014.json) 保留，不覆盖。

两轮业务与计数一致：MAF 与顺序采购适配器同为 26/30 完成、4 个安全停止，无局部修复为 10/30；MAF 工具查询从 326 减至 281，但两轮本地 p50 均高于顺序适配器，未证明端到端加速。R2 在没有并行构建/回归负载时运行，时延仅描述性，不能作因果收益。两轮模型调用与提供方费用均为零。

## 固定数据与对照

数据为 [30 个合成采购任务](../agents/python/evals/procurement_data/tasks.json)，18 个开发场景、12 个留出场景，每个场景拥有独立商品、库存、优惠券和评论。人审状态保留为 `pending_user_review`。多次重复是同一场景的重复测量，不能当作新的独立样本。

| 组 | 实现 | 用途 |
| --- | --- | --- |
| sequential_adapter | 旧 `ready[0]` 顺序推进思路的采购适配器；沿用成功回执和 TTL，需求或依赖对象整体变化使下游重新取证 | 主策略对照，不声称旧 query API 原生支持采购 |
| maf | 新 MAF 拓扑层、最多两路执行、完整层检查点、DB 步骤回执、一次局部修复 | 当前工程实现 |
| no_parallel | 同一真实 MAF 运行器，仅将并发上限改为 1 | 并行调度消融 |
| no_local_repair | 同一真实 MAF 运行器，仅将自动修复次数改为 0 | 自动修复消融 |

四组使用相同确认输入、候选检索、报价规则、库存与评论证据、生产最终核验和独立评分器。评测中的专业执行器直接调用 `execute_capability`，不走专业服务 HTTP，也不调用大模型。正式页面的跨服务路径需要另行端到端验证。

顺序适配器与 MAF 都写入相同 PostgreSQL 步骤回执，避免将纯内存脚本当作持久化服务的性能对照；MAF 额外检查点和图调度仍有成本。本地快速 SQL 工具上的时延不外推到远程模型或多服务生产环境。

故障只在 `after_discovery`、`after_capability`、`before_finalize` 这些逻辑事件和固定次数触发，不靠墙钟睡眠。每个 trial 保存计划事件、实际触发索引和执行轨迹；二次核验前的故障只有实际进入二次核验时才有机会触发，报告会保留未触发事件。

## 业务规则与独立 oracle

领域实现为 [domain.py](../agents/python/shared/procurement/domain.py)，评测评分为 [procurement.py](../agents/python/evals/procurement.py) 中的 `reference_check`。

- 每个备选是一个 SKU × 确认数量，所有备选分别与同一货款预算比较。两件备选不是一个合并购物篮。
- 金额统一使用 CNY 和 Decimal。以商品当前价格为基数，`single_coupon_v1` 只选择最佳一张合法固定券或百分比券；检查归属、品类、门槛、生效与过期时间、次数上限和折扣封顶，不叠会员优惠、促销或第二张券。
- 商品检索不按折前单价与数量裁剪潜在优惠商品；最终实时复算优惠后金额。
- 库存总数量必须覆盖确认数量；只检查，不锁定库存、不消费优惠券、不下单。
- 评论包含真实行 ID、原始标题、正文与评分。风险引用只解释低评分或已标记的评论；没有原文时明确证据不足，不以关键词命中冒充中文情感模型。
- 回执包含能力、`tool:<capability>` 来源、观察时间、最长五分钟有效期、来源 ID、报价规则摘要和哈希。哈希用于定位规则版本，**不是签名**；可信来源边界由已认证 host 捕获的工具返回值和数据库回执保证，不能接收模型正文冒充回执。
- 能力指纹按实际依赖计算。降预算不让报价、库存或评论事实自动失效；最终 oracle 会重新判断可行性。数量变化影响报价与库存，查询变化必须重新检索，再决定哪些旧 SKU 证据仍可复用。
- 最终 oracle 重新读取数据库，同时检查商品范围、来源、时效、金额、库存、券与评论内容。独立评分器再按原始 SQL 和数据集允许集合评分，允许合法替代商品，不要求唯一轨迹。

无评论、库存不足或修复用尽后的安全停止不是“任务成功”。报告分别记录 `task_success`、`safe_noncompletion`、`safety_violation`、异常、预算/修复上限、工具调用和复用次数。

## 复验

在仓库根目录执行，输出路径必须是新文件；评测器拒绝覆盖已有报告，也不接受外部 DSN：

```bash
source scripts/dev-env.sh
APP_ENV_FILE=/dev/null RECORD=false LLM_PROVIDER=replay \
OPENAI_API_KEY= AZURE_OPENAI_KEY= AZURE_OPENAI_API_KEY= \
DEEPSEEK_API_KEY= MOONSHOT_API_KEY= \
uv run --project agents/python --no-sync python -m evals.procurement \
  --output docs/evaluation/procurement-NEW-RUN.json
```

评测自行启动 `pgvector/pgvector:pg16` testcontainer、创建 `procurement_eval`，仅在该隔离容器内重建各策略同源数据，结束销毁容器。保留 Ryuk；不要为了复验关闭 reaper 或把主库 DSN 接进来。

源码 manifest 记录实际文件 SHA-256、执行时 Git HEAD、工作树是否有未提交内容，以及评测期间源码是否变化。未提交实现的结果不能仅用旧 HEAD 充当源码版本。每个场景的四组结果和失败都会进入 `trials`，不筛选漂亮结果；原售后、检索、任务与遥测历史报告保持不变。

目标测试：

```bash
APP_ENV_FILE=/dev/null RECORD=false LLM_PROVIDER=replay \
OPENAI_API_KEY= AZURE_OPENAI_KEY= AZURE_OPENAI_API_KEY= \
DEEPSEEK_API_KEY= MOONSHOT_API_KEY= \
uv run --project agents/python --no-sync python -m pytest \
  agents/python/tests/test_procurement_domain.py \
  agents/python/tests/test_procurement_evaluation.py -q
```

## 追加合成演示数据

已有 showcase 专用库和合成客户后，可运行：

```bash
PYTHONPATH=agents/python:. uv run --project agents/python --no-sync \
  python -m scripts.seed_procurement
```

使用当前专用展示服务的 `DATABASE_URL`；脚本仅接受 `current_database() == reliable_commerce_demo` 和既有 `customer@example.test` 客户。它追加五个固定合成商品、一张本人单券、原文评论和库存，不删除旧订单或任务，重复执行也不恢复已经变化的库存、用券次数或过期时间。

主例为“采购专用耳机，3 件，2 个备选，货款预算 1500 元”；将预算降至 1200 元后有两项更低价合成备选可供重新取证。该数据用于工程展示，不代表真实商品、订单或推荐收益。

## 验收口径

固定边界用例要求全部通过：未确认执行、跨用户越权、过期或伪造证据导致完成、并行回执覆盖、阶段中断后重复接受旧结果、需求版本变化后旧图恢复、超过一次自动修复或逻辑任务 24 次模型调用，均为零容忍。

当前真实模型费用尚未获本轮授权。后续如获授权，先在开发集建立同模型、同工具、同确认需求和同预算的基线，再冻结规则测留出集。可采用“可行留出场景最终核实成功率 ≥80%、可修复动态场景 ≥70%”作为**设计目标**；这不是当前结果、预测收益或统计显著性承诺。费用、有效价格表和活动预算必须重新明确，不沿用旧活动额度。生产转化、满意度、GMV、ROI 和 SLA 尚未测量。
