# 2026-10-14 采购运行时本地验收

本轮在 `feat/procurement-runtime` 从 `be8a5e6` 继续开发。采购需求、单券报价、MAF分层执行、局部修复、步骤回执、SSE与中文工作台已经接通。未执行付费模型调用、真实下单、库存占用或公网发布。

| 检查 | 实际结果与边界 |
|---|---|
| 后端完整回归 | 1159 passed、3项真实模型测试跳过、154条既有测试密钥/依赖警告；190.73秒 |
| 前端完整回归 | 38个测试文件、236 passed；TypeScript通过 |
| 静态检查 | 后端Ruff通过、332文件格式检查通过；改动脚本按后端配置检查通过；前端0 errors、64条既有warnings |
| 运行器专项 | 27 passed；真实MAF与PostgreSQL，含跨进程退出、暂停竞争、租约fencing、TTL恢复、最终oracle持久化 |
| 采购领域和评测专项 | 一次完整52 passed；该集合包含在全后端回归内，不重复相加 |
| 模型调用边界 | 用预设响应驱动真实MAF工具循环；伪造正文无工具被拒绝，成功取证后仅1轮模型请求；HTTP替身验证24次累计上限及旧spec/lease零发出 |
| 本机真实浏览器 | 3010经真实专业HTTP与数据库；预算1500→1200、刷新恢复、393px窄屏通过，页面/控制台无错误 |
| 容器构建与源码 | 后端与Next.js生产镜像构建成功；151份Python源、150份前端构建源核对一致；9服务健康，仅本机3020 |

上述浏览器预算修改发生在首次任务完成之后，通过显式“修订需求”进入新版本。首次货款1260/1470，修订后1110/1140，自动修复1次、累计复用7步、模型调用0。运行中的暂停与晚到结果由独立后端故障测试覆盖，未把完成后修订描述为运行中暂停。

## 采购任务对照

[最终版本120条原始记录](evaluation/procurement-20261014-r2.json)和[结果说明](evaluation/procurement-20261014-r2.md)保留全部成功、停止与失败信息，并记录执行时源码散列。

| 策略 | 完成 | 受控停止 | 安全违规 / 异常 |
|---|---:|---:|---:|
| 旧顺序推进策略的采购适配器 | 26/30 | 4/30 | 0 / 0 |
| 新MAF分层执行 | 26/30 | 4/30 | 0 / 0 |
| 新执行器关闭并行 | 26/30 | 4/30 | 0 / 0 |
| 新执行器关闭局部修复 | 10/30 | 20/30 | 0 / 0 |

MAF组相对顺序适配器的工具查询从326次变为281次，复用从6次变为51次。单个trial的时延p50为顺序244.05毫秒、MAF867.52毫秒、无并行940.25毫秒。当前未证明端到端加速，不能宣称达到20%目标；最终R2在全量回归与构建结束后运行，时延仍只作描述性记录。首轮在共享负载下执行，也完整保留。

这是确定性工程评测：真实数据库、真实MAF，专业HTTP在评测适配器中由同一领域函数替代；正式页面另有真实跨HTTP验收。模型调用和费用均为0，不能用26/30代表真实模型采购成功率。88条成功trial均持久化最终核验回执，安全停止与任务成功分开计算。

## 最终版本与部署复验

最后修复了模型工具已知业务错误被SDK转换成文本、丢失修复对象的问题。回归先复现失败，再验证原错误码和SKU传递、且仅一轮模型请求；未访问模型网络。随后完整后端为1159通过，生产镜像重建，生成R2评测以重新绑定实际源码。首轮报告与日志保留，没有修改用例、评分或择优覆盖。

最终3020生产浏览器也通过：同一任务两次需求版本、一次局部修复与零模型调用；生产录屏19.12秒。整栈stop/up保留同一采购任务（spec2/plan3）、旧查询任务及旧订单，逐项API响应散列不变，最终oracle回执仍存在。3010开发演示和3020展示使用不同数据卷，跨实例任务404不能解释为丢失数据。

## 证据与复验

- [中文演示与实际观察](procurement-demo.md)
- [执行语义、版本与恢复](procurement-architecture.md)
- [评测入口、对照及适用范围](procurement-evaluation.md)
- 增量迁移为005；保留旧数据、旧报告和用户学习资料。
- 真实模型质量、费用收益与生产容量仍未测量。80%成功、70%修复、20%延迟改善是计划目标，尚未作为实测成果声明。

---

# 2026-10-01 全量本地升级验收

本次验收覆盖 P0/P1/P2 的本地代码、中文体验、隔离实验、容器部署及展示材料。真实模型付费复测和公网发布按当前范围留待另行决定，没有把跳过项算作通过。

| 检查 | 实际结果与范围 |
|---|---|
| 后端完整回归 | 1039 passed，3 项需要真实模型凭据的测试跳过，154 条测试夹具/依赖警告 |
| MCP 独立工作区 | 商品 25 passed，库存 21 passed，真实隔离数据库 |
| 前端完整单元测试 | 36 个文件、211 passed；TypeScript 通过 |
| 前端静态检查 | 0 errors，64 条既有 warnings；没有降低规则等级 |
| 后端静态检查 | Ruff 通过，307 个文件格式检查通过 |
| 文档 | 108 页检查通过、没有断链；新增主线页面的导航/实体生成合同通过 |
| 教程契约 | 34/34 通过 |
| 容器构建与源码 | 后端及 Next.js 生产镜像构建成功；容器 137 份 Python 源与 146 份前端构建源摘要核对一致 |
| 浏览器 | 真实售后审批、丢响应恢复、缺证据、固定任务与过期后重新核实通过；桌面和393px窄屏检查通过 |
| 部署恢复 | 9个常驻服务健康，仅发布本机3020；整栈stop/up后同一任务、退货记录保持不变 |
| 累计预算 | 6服务共用同一POSIX卷；独立合成探针验证并发预留与容器重启不回收未知费用，没有真实模型请求 |
| 真实遥测 | Collector 收到 95 个 span；四阶段 HTTP→任务→SQL 链及独立跨服务只读探针通过，指标计数和低基数标签核对通过 |
| 展示 | 96.76 秒真实中文页面录屏、字幕、讲解稿及截图；原始资料、历史评估和旧截图均保留 |

完整回归首轮发现了配置测试隔离、旧 opaque 会话标识兼容和检索数据集格式冲突，修复后完整回归通过；随后补充遥测生命周期回归，最终全套结果为上表所列。未将这些失败隐藏或改成跳过。后端警告主要来自测试短密钥和依赖弃用；运行中的展示环境使用单独随机生成的密钥。

## 新能力的验收入口

- 管理员审批与可信申请人：`tests/test_approval_authorization.py`，售后入口/恢复/编排回归。
- 任务实体验收、人工复核与恢复：`tests/test_task_acceptance.py`、`tests/test_task_state.py`。
- 可修订上下文、专业智能体一致性及晚到证据隔离：`tests/test_context_epochs.py`、`tests/test_context_lifecycle.py`。
- 检索过滤和独立评估：[当前检索报告](evaluation/retrieval-20261001-r2.json)及[范围说明](retrieval-evaluation.md)。离线哈希向量只验证工程路径；混合路径的未知品类无结果案例保留失败，不能宣称真实语义效果。
- 外部履约模拟：`tests/test_fulfillment_outbox.py`，真实本机HTTP、独立SQLite、进程退出和只读对账。
- 任务观测：`tests/test_task_observability.py`、`tests/test_telemetry_lifecycle.py`，覆盖观测故障隔离及真实 ASGI 生命周期插桩。[本机 OTLP 验收](local-telemetry-verification.md)记录实际 Collector 采集、链路父子关系、指标和隐私探针；默认展示环境现已关闭遥测并移除临时 Collector。
- 非破坏性录制数据：`tests/test_recording_order.py`，新建合成订单且保留旧订单、审批及任务。

## 固定任务的本机并发观察

最终数据经 Next.js 代理、真实HTTP API和PostgreSQL，使用明确标注的固定计划，不调用模型。每个并发条件12个任务全部完成并核验回执。

| 并发 | 成功/任务 | p50(ms) | 描述性p95(ms) |
|---|---:|---:|---:|
| 1 | 12/12 | 43.76 | 103.74 |
| 3 | 12/12 | 69.14 | 81.42 |
| 5 | 12/12 | 87.38 | 158.54 |

[逐任务结果与源码散列](evaluation/task-benchmark-20261001-final.json)。样本少，条件间不构成优化前后对照；不外推生产吞吐、SLA或模型延迟。首次宿主直连观察也保留为历史文件，没有挑选最好一次覆盖原结果。

## 复验与版本关系

`scripts/verify_portfolio.sh` 提供完整本地代码/测试/构建/文档门禁；先停止同目录的Next开发服务器。浏览器、售后故障和检索评估按各自指南独立运行，报告写新文件，不覆盖旧结果。

Windows/WSL 展示建议使用 `bash scripts/showcase.sh serve` 保持前台会话；本次已实测该方式启动，Windows 访问 `http://localhost:3020/login` 返回 200 和中文页面。Ctrl-C 停止容器并保留数据卷，具体说明见[本机容器展示](local-showcase.md)。

报告保留执行时的基线HEAD和工作树状态，并绑定实际源码散列。后续保存提交不会回填或篡改原测量元数据；应核对逐文件散列，而不是把基线提交号当成本轮新增代码的版本。

---

# 验证与复现记录

日期：2026-10-01。本轮已授权修改代码并完成 P0/P1/P2 本地升级及本地测试；未进行付费模型调用或公网发布。

## 当前售后证据与版本边界

本节记录评估执行时未提交工作树的实测结果。执行时分支为 `feat/portfolio-upgrades`，HEAD 是 `d8af1bf3b26f185f9fc57017d5f1e1d20e1389b9`，但 `worktree_dirty=true`。**不能把该 HEAD 当成新增实现的源码版本**：实际被测文件以新报告的逐文件 `implementation_sha256` 为准，评估前后核对一致。报告生成时间为 `2026-10-01T03:39:50.357021+00:00`。

| 检查 | 本轮实际结果 | 范围 |
| --- | --- | --- |
| 审批、售后边界、恢复、HITL 关联、本地履约回归 | 87 passed，47.12 秒 | 6 个相关测试模块；真实隔离 PostgreSQL/Redis 与本机 HTTP 供应商 |
| 新管理员审批负例 | 修改前 3 条按预期失败；修改后专用 7 条通过 | 客户本人、其他客户、卖家不能审批；执行恢复原申请人身份 |
| 冻结基线来源核验 | 6/6 旧散列恢复；6/6 当前 manifest 匹配；6/6 去文档字符串 AST 相同 | 历史 Git 对象与 2026-09-16 发布快照双重核对 |
| 当前售后受控评估 | 396/396 trial 已执行并保留；结果见下表 | 6 个版本 × 22 类场景 × 3 次重复，另有 300 次本地服务负载请求 |
| 限定改动文件静态检查 | Ruff、格式与 diff 检查通过 | 不代替全仓或远端 CI |

本节表格仅覆盖售后阶段；全量后端、前端与浏览器的最终集成验收见本文顶部记录。下面的 2026-09-16 数字和截图仅属于历史验收。

当前报告：[after-sales-20261001.json](evaluation/after-sales-20261001.json)；基线审计：[after-sales-baseline-audit-20261001.json](evaluation/after-sales-baseline-audit-20261001.json)。旧的 [after-sales-results.json](evaluation/after-sales-results.json) 完整保留，文件 SHA-256 为 `0ae5d7fd5b7a6a825fb71d7ea3e62b6947a2bf48f165ae6e232cbeb1ece5f1b4`，未覆盖、未替换 hash。

## 当前受控对照：全部 396 次 trial

固定业务时钟仍为 `2026-09-16T12:00:00+00:00`，目的是保持与历史场景的时间边界一致，**不是本轮执行日期**。使用一次性 `isolated_after_sales_eval` 数据库，每次 trial 重置该专用数据库的合成数据。22 类场景、6 个版本及 `trial()` 评分 AST 与本轮改动前 HEAD 一致，没有删除失败样本或改通过条件。

| 版本 | 满足预定义场景条件 | 违规写入 trial | 重复业务效果 trial |
| --- | ---: | ---: | ---: |
| B0 | 36/66 | 15 | 0 |
| B1 | 51/66 | 0 | 0 |
| B2 | 66/66 | 0 | 0 |
| A_no_recheck | 45/66 | 18 | 0 |
| A_no_reconcile | 63/66 | 0 | 0 |
| A_no_budget | 63/66 | 0 | 0 |

- B0：上游衍生的早期退货工具基线。
- B1：统一政策与事务的 M2 基线。
- B2：当前持久操作与恢复实现。
- 三个消融版本分别移除提交前复核、提交结果核实、统一尝试预算，且只在独立评估进程生效。

下面列出全部未通过的场景及次数，逐次输入、结果、故障触发数和耗时保留在 JSON 中：

| 版本 | 未通过场景 |
| --- | --- |
| B0 | `conflicting_delivery` × 3；`expired_old` × 3；`expired_one_second` × 3；`future_delivery` × 3；`lost_reply` × 3；`missing_delivery` × 3；`parallel_same` × 3；`payload_conflict` × 3；`persistent_db` × 3；`temporary_db` × 3 |
| B1 | `lost_reply` × 3；`parallel_same` × 3；`payload_conflict` × 3；`persistent_db` × 3；`temporary_db` × 3 |
| B2 | 无 |
| A_no_recheck | `conflicting_delivery` × 3；`expired_old` × 3；`expired_one_second` × 3；`future_delivery` × 3；`missing_delivery` × 3；`not_delivered` × 3；`parallel_distinct` × 3 |
| A_no_reconcile | `lost_reply` × 3 |
| A_no_budget | `persistent_db` × 3 |

相较历史报告的判定变化：B0 的 `parallel_same` 第 1 次：历史通过，本次失败。并发调度可能改变同一基线的返回组合；这里保留第一次完整重跑的实际结果，不挑选较好的次数。

本次测试对象是退货工具/服务，不含 LLM、生产流量或真实退款。评估中显式关闭 HITL 总开关、设金额阈值 500，合成订单金额 79；新增履约模拟也关闭。因此 **B2 的通过计数不能证明审批权限或外部履约可靠性**，这两部分由上面的独立集成回归覆盖。通过数不是模型准确率或生产成功率；重复 trial 不视为独立用户样本。

## 当前本地服务负载观察

每个并发条件使用 100 个不同的合成订单。时延从获得并发槽位开始，未包含排队等待、模型、公网、审批等待或履约 HTTP。未运行优化前后的配对性能实验，因此不宣称性能提升。

| 并发 | 成功 / 请求 | 观察吞吐（次/秒） | 服务 p50（ms） | 服务 p95（ms） |
| --- | ---: | ---: | ---: | ---: |
| 1 | 100/100 | 121.91 | 8.05 | 8.972 |
| 3 | 100/100 | 253.89 | 9.877 | 11.909 |
| 5 | 100/100 | 354.39 | 11.225 | 16.25 |

这是本机、有限合成请求的服务微基准，不是生产容量或 SLA。

## 当前环境、日志与复验

Python 3.12.13；平台：Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.35。依赖锁文件及核心源码散列在当前 JSON 内。

| 已安装依赖 | 实际版本 |
| --- | --- |
| agent-framework-core | 1.14.0 |
| agent-framework-openai | 1.13.0 |
| asyncpg | 0.31.0 |
| testcontainers | 4.15.0 |
| pydantic | 2.13.4 |

评估父/子进程均使用 `APP_ENV_FILE=/dev/null`、`RECORD=false` 与清空的云模型密钥；子进程还显式使用 `LLM_PROVIDER=replay`。保留 testcontainers 的默认 reaper，没有连接或重置主数据库。原始父进程日志保存在本地会话工作区 `work/after-sales-20261001.log`，7 组子进程标准输出与错误日志位于 `work/after-sales-20261001-raw/`；87 项回归日志为 `work/p0-p2-backend-tests.log`。

复验时使用新的文件名，避免覆盖本次或历史报告：

```bash
source scripts/dev-env.sh
evaluation_stamp=$(date +%Y%m%d-%H%M%S)
APP_ENV_FILE=/dev/null ENVIRONMENT=test RECORD=false LLM_PROVIDER=replay \
  OPENAI_API_KEY= AZURE_OPENAI_KEY= AZURE_OPENAI_API_KEY= \
  DEEPSEEK_API_KEY= MOONSHOT_API_KEY= TYPESAFE_API_KEY= \
  FULFILLMENT_SIMULATION_ENABLED=false \
  uv run --project agents/python --no-sync python -m evals.after_sales \
  --output "docs/evaluation/after-sales-${evaluation_stamp}.json" \
  --log-dir ".local/after-sales-${evaluation_stamp}-raw"

APP_ENV_FILE=/dev/null ENVIRONMENT=test RECORD=false LLM_PROVIDER=replay \
  OPENAI_API_KEY= AZURE_OPENAI_KEY= AZURE_OPENAI_API_KEY= \
  DEEPSEEK_API_KEY= MOONSHOT_API_KEY= TYPESAFE_API_KEY= \
  uv run --project agents/python --no-sync python -m pytest \
  agents/python/tests/test_approval_authorization.py \
  agents/python/tests/test_after_sales_entries.py \
  agents/python/tests/test_after_sales_recovery.py \
  agents/python/tests/test_orchestration_resume.py \
  agents/python/tests/test_chat_hitl_linkage.py \
  agents/python/tests/test_fulfillment_outbox.py -q
```

本地履约实验见 [履约模拟说明](fulfillment-simulation.md)，真实模型保留集、真实费用、公网体验部署与真实外部退款不在本节验收范围内。

## 2026-09-16 历史记录（保留）

以下为当时验证结果与复验命令，仅用于追溯。其“当前”“完整测试”均指当时版本；不要用历史命令覆盖旧 JSON。原链接与截图保留。

日期：2026-09-16。此记录区分实际运行、受控评估和未验证事项。

### 验证结果

| 检查 | 实际结果 |
| --- | --- |
| Python 完整测试 | 906 passed，3 skipped，2 个依赖警告 |
| Python Ruff / 格式 | 全部通过；冻结的历史基线源文件明确排除格式重写 |
| 前端单元测试 | 179 passed，30 个文件 |
| TypeScript | 通过 |
| 前端 ESLint | 0 errors，64 条既有 warnings |
| Next.js 生产构建 | 通过 |
| 真实浏览器 | 3 个流程通过，桌面与手机布局检查通过 |
| 文档 / 补丁 | 链接与 git diff 检查通过 |

真实模型的 3 项测试明确跳过；运行中显式清空云端模型凭据。没有真实支付、退款或外部业务数据。

### M4 受控对照

22 类场景，每类重复 3 次，每个版本 66 次 trial。每次重置专用 PostgreSQL 数据，固定业务时钟，
源文件在执行前后核对 LF 换行规范化散列；故障没有触发的 trial 不计为成功恢复。

| 版本 | 满足预定义场景条件 | 违规写入 trial | 重复业务效果 trial |
| --- | ---: | ---: | ---: |
| B0 | 37/66 | 15 | 0 |
| B1 | 51/66 | 0 | 0 |
| B2 | 66/66 | 0 | 0 |
| A_no_recheck | 45/66 | 18 | 0 |
| A_no_reconcile | 63/66 | 0 | 0 |
| A_no_budget | 63/66 | 0 | 0 |

- B0：冻结的上游退货工具基线。
- B1：M2 统一规则版本。
- B2：当前持久操作与恢复实现。
- A_no_recheck：在独立评估进程中关闭提交前政策复核。
- A_no_reconcile：关闭提交结果核实。
- A_no_budget：关闭统一尝试预算，由评估器外部时限终止。

这些是场景条件的通过计数，不是模型准确率或生产成功率。原版已经有行锁和部分防重，不能把所有
防重复能力归为本分支创新。主要新增证据是规则一致性、原子回执以及对已提交但未收到响应的确认能力。
并发场景受调度影响，基线计数可能在重跑时略变；保留逐次结果，不挑选最好一次。
重复运行具有相关性，不把 66 次 trial 当作 66 个独立用户样本。该集合是工程回归集，不是盲测模型保留集。

完整逐案例结果、故障触发数、基线散列和实际实现散列见 [JSON 证据](evaluation/after-sales-results.json)。

### 本地服务负载观察

每个并发条件使用 100 个不同的合成订单，测量提交服务与本地数据库。时延从进入并发槽位开始计算，
不包括客户端等待槽位、模型、公网或真实外部服务。

| 并发 | 成功 / 请求 | 观察吞吐（次/秒） | 服务 p50（ms） | 服务 p95（ms） |
| --- | ---: | ---: | ---: | ---: |
| 1 | 100/100 | 134.71 | 7.232 | 8.137 |
| 3 | 100/100 | 282.01 | 8.851 | 10.465 |
| 5 | 100/100 | 384.76 | 10.34 | 12.974 |

单机、单次有限样本不能外推生产容量或 SLA，部署前仍需独立负载与故障验证。

### 浏览器验收

环境：`http://localhost:3010`，API `http://localhost:8180`，独立演示数据库。
使用仓库 Playwright；桌面 1280×720、审批页面 1440×1000、手机 393×852。

| 项目 | 证据 |
| --- | --- |
| 页面身份与非空 | 实际登录、订单 URL 与页面内容断言通过 |
| 框架 / 运行错误 | 正常流程无未捕获页面异常，截图无框架错误覆盖层 |
| 申请与审批 | 提交时没有退货记录，管理员批准后出现确认回执 |
| 响应丢失 | 请求实际到达后端后丢弃响应，浏览器保留的操作标识仍可查到等待状态 |
| 缺签收证据 | 显示人工核实，未显示创建成功 |
| 响应式 | 手机视口无文档横向溢出，回执可滚动查看 |

HTTP 400/409 的业务拒绝和人为注入的网络失败属于预期验证事件，不当作应用未捕获异常。
截图仅含合成账户、订单、商品和地址。真实模型聊天、生产支付与公网部署未做浏览器验收。

[等待审批](images/reliable-commerce-pending.png) · [审批页面](images/reliable-commerce-approval.png) · [确认回执](images/reliable-commerce-completed.png) · [手机页面](images/reliable-commerce-mobile.png)

### 复验命令

```bash
OPENAI_API_KEY= AZURE_OPENAI_KEY= AZURE_OPENAI_API_KEY= RECORD=false uv run --project agents/python --no-sync pytest agents/python/tests -q
uv run --project agents/python --no-sync ruff check agents/python
uv run --project agents/python --no-sync ruff format --check agents/python
uv run --project agents/python --no-sync python -m evals.after_sales --output docs/evaluation/after-sales-results.json
pnpm --dir web exec vitest run --maxWorkers=1
pnpm --dir web exec tsc --noEmit
pnpm --dir web lint
pnpm --dir web build
```

浏览器复验先按 [演示说明](portfolio-demo.md) 启动并重置合成数据。真实模型试验需先确定服务与费用上限。

### 未完成的验证

GitHub Actions 当前仍关闭：提供 `portfolio-ci.yml` 配置，但没有宣称远端 CI 已执行。
真实模型保留集、模型成本、生产性能和跨系统退款均未验证；这些限制不被离线测试通过替代。
