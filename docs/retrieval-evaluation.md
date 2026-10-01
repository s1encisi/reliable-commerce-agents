# 检索评测与能力边界

当前检索采用 PostgreSQL 全文检索、pgvector 和 RRF 融合。2026-10-01 的新增验证覆盖结构化过滤、中文品类别名、词干变化、精确型号、无结果和嵌入不可用时的降级。

本次结果见 [逐案例 JSON](evaluation/retrieval-20261001-r2.json) 与 [运行解读](evaluation/retrieval-20261001-r2.md)。数据和相关性标签由助手编写，尚待用户人工审核；它是合成工程验收集，不是线上用户样本或真实语义模型排行榜。

## 三层边界

1. **结构化过滤是服务端约束。** 分类、最低／最高价格、最低评分及商品有效性在词法与向量候选阶段共同生效。嵌入不可用时仍使用同样的过滤条件，结果明确标记词法降级。价格沿用目录的原始货币单位，不隐含换算。
2. **检索产生候选，不证明需求已完成。** 英文全文检索按 OR 召回词元，中文通过有限品类别名补充；向量路径总是寻找近邻，再由 RRF 融合。型号首位正确仍可能带出相似型号，合法商品、正确价格和非空候选均不足以证明推荐满足用户意图。
3. **真实语义效果需要另一类证据。** 本轮嵌入使用确定性词元哈希，仅用于离线执行真实 pgvector 链路，没有训练得到的同义词理解能力。当前自由目标的任务仍需用户核实；后续获得真实嵌入模型授权后，才能在人工审核的数据上校准相关性与拒答阈值。

本轮混合路径的无结果正确率为 **2/3**。目录外查询“quantum teleportation device”仍返回三个无关近邻，该失败保留在报告中。没有为了让哈希向量通过此例而新增固定阈值；这种阈值不能直接迁移到真实嵌入模型。未知意图应保留待确认状态，不能把“找到了候选”显示为“问题已解决”。

## 数据模式与版本

检索数据使用独立的 evals/retrieval_data/zh.json 对象模式，包含商品、查询和分级相关性；
它与 evals/datasets 下的聊天工具黄金数据数组属于不同契约，分别由各自 loader 和测试验证。

当前链接为完整回归后修正数据目录的 r2 复验。首轮 [原始报告](evaluation/retrieval-20261001.json)
和 [原始解读](evaluation/retrieval-20261001.md) 保留；r2 没有修改商品、查询或相关性标签，
新的源码清单反映独立数据路径及当前评测器。

## 固定评测协议

- 12 件合成商品、12 条查询；9 条有相关商品，3 条预期为空。
- 每条查询固定 k=3；路径为当前版本纯词法功能消融、混合检索＋离线哈希嵌入、嵌入不可用后的词法降级。
- 每条路径、每个案例热身 1 次并测量 3 次；重复不是独立样本，不据此报告统计显著收益。
- 保留 Recall@3、nDCG@3、Precision@3、实际返回集精度、空结果正确性、逐条约束违规、路径标记、重复排名一致性和延迟。
- 空相关集的 Recall/nDCG 为 null，以空结果正确性单独计分；Precision@3 的分母固定为 3。
- 基线是同一版本的功能消融，没有声称改造前历史版本的指标。
- 本地顺序耗时包含离线嵌入计算，不能外推生产容量或 SLA。

## 复验

在仓库根目录、现有锁定依赖环境中运行：

~~~bash
APP_ENV_FILE=/dev/null RECORD=false LLM_PROVIDER=replay \
  uv run --project agents/python --no-sync python -m evals.retrieval \
  --output docs/evaluation/retrieval-rerun.json --repetitions 3 --warmups 1
~~~

入口会禁用私人环境文件、录制和模型凭据，创建独立 PostgreSQL 容器，不接受主数据库 DSN。已存在的输出文件不会覆盖；再次运行请选新文件名。源码在运行前后变化时不保存报告。

~~~bash
APP_ENV_FILE=/dev/null RECORD=false LLM_PROVIDER=replay OTEL_ENABLED=false \
  uv run --project agents/python --no-sync python -m pytest \
  agents/python/tests/test_retrieval_evaluation.py -q
~~~

JSON 同时记录数据来源、环境版本、运行时 Git HEAD、工作区是否有改动及源码 SHA-256。工作区未提交时，Git HEAD 不是新实现的唯一标识，必须同时核对源码哈希。
