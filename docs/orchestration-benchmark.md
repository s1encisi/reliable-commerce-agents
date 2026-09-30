# 编排模式基准测试

同一个问题，五种路由方式，并附上数据。

本仓库的核心论点是：同一个业务域可以用五种不同方式编排，而且这个选择会产生真实后果。这个论断在这里被提了很久，却从未被量化。本页就是这次量化。

## 测试条件

复现（或质疑）本结果所需的全部信息：

| | |
|---|---|
| **模型** | `gpt-4.1`（Azure OpenAI） |
| **技术栈** | Python 后端，本地 Docker Compose |
| **提示词** | 4 条（商品检索、订单状态、购买建议、退货申请） |
| **重复次数** | 每条提示词每种模式 2 次 —— 共 40 次请求 |
| **请求间隔** | 8 秒；对话路由位于 Redis 滑动窗口限流器之后，连续快速发送测到的是 429 而不是编排行为 |
| **测量时间** | 2026 年 8 月 27 日，提交 `b53d20b` |
| **运行框架** | [`agents/python/evals/benchmark_modes.py`](https://github.com/s1encisi/reliable-commerce-agents/blob/main/agents/python/evals/benchmark_modes.py) |

该运行框架驱动的是 `POST /api/chat`，而不是在进程内直接调用各模式，因此每一次运行都会经过认证、护栏、内容净化、事实核验与用量日志 —— 走的是真实路径，而非其副本。它无法在 `LLM_PROVIDER=replay` 下运行：回放夹具会瞬时返回，使延迟数据失去意义。

## 结果

| 模式 | p50 | p95 | 回复长度 | LLM 调用 | 实际执行内容 |
|---|---|---|---|---|---|
| `tool` | 10.6 秒 | 21.4 秒 | 878 字符 | 编排器 + 专业智能体 | orchestrator、order-management、product-discovery |
| `handoff` | 5.1 秒 | 7.2 秒 | 970 字符 | 分诊 + 专业智能体 | order-management、product-discovery |
| `group-chat` | 3.2 秒 | 10.8 秒 | 110 字符 | 2 位参与者 + 主持人 | value、quality、moderator |
| `workflow:pre-purchase` | 0.26 秒 | 0.28 秒 | 127 字符 | **无** | reviews、stock、price_history、shipping |
| `workflow:return-replace` | 0.10 秒 | 0.11 秒 | 82 字符 | **无** | check_eligibility |

## 请仔细看最后两行

**工作流模式完全没有调用 LLM。** 它们是建立在工具调用之上的确定性图，其「建议」是一段格式化字符串，而不是生成的文本。拿 0.26 秒与 `tool` 的 10.6 秒相比、并得出「工作流快四十倍」的结论是错误的 —— 它们做的是不同的工作。这个数字诚实的含义是：*当答案可以由工具输出直接拼装、无需模型参与时，成本就是毫秒级。*

这是一个真正有用的结论。它不是对 `tool` 模式的延迟优势，而是一个提醒：要意识到自己什么时候其实不需要模型。

**`workflow:return-replace` 只走到了 `check_eligibility`。** 退货提示词所用的种子订单处于 `shipped` 状态，而退货要求 `delivered` —— 因此工作流正确地拒绝了，并在第一道门就停下。那 0.10 秒测到的是一次拒绝，而不是一次完整的退货流程。这个数字是真实的，但它不具备代表性。

## token 与成本大多缺失，这是刻意的

只有 `tool` 模式写入了用量记录：**在 gpt-4.1 的价格下，每次运行 7,106 token 与 0.0167 美元**。
其余所有模式都报告为*未采集*，运行框架把「未采集」与「零」区分开来，因为这是两种截然不同的结论。

这个缺口是真实存在的：通过 MAF 工作流事件流式输出的模式，目前不会像工具路由那样写入 `usage_logs` 记录。把它们报成 `$0.00` 会让这张表看起来完整，但那是撒谎。填补该缺口已作为一项独立工作跟踪。

## 测量过程中实际改变了什么

这五种模式里，有两种在首次尝试基准测试时是坏的，而正是这次尝试证明了这一点。

**`handoff` 曾经每轮耗时 100–200 秒、输出 19,000–25,000 个字符。** 现在是 5.1 秒、970 个字符 —— 是本表中最快的 LLM 模式。原因不在性能：该网格的起始智能体是*工具路由*编排器，它带着 `call_specialist_agent` 和一份点名该工具的提示词，因此它从未调用处理权交接工具。随后 MAF 的自主模式给它喂了一个续写提示词并重新运行它，默认上限高达 50 轮。修复前实测：流式输出 5,403 次更新，从未调用任何专业智能体。

**`workflow:pre-purchase` 从一个四执行器扇出中只返回了 48 个字符**：
`Stock: 348 units available | Price trend: stable`。扇出是真实的；问题在于综合环节读取的字段名，其自身的工具从未返回过（用 `sentiment` 去取 `overall_sentiment`，用 `options` 去取 `shipping_options`）。每一行都有守卫分支，因此四路输入中有两路在没有任何报错的情况下消失了，而且自该工作流写出以来每次运行都是如此。现在它返回全部四路：

```
Reviews: very_positive (4.7/5 avg) | Stock: 203 units available |
Price trend: stable | Shipping: from ¥5.99, 5-7 business days
```

如果直接发布首次运行的数据，就会交付一张把两个坏掉的模式描述成设计特性的表。

## 如何选择模式

- **`tool`** —— 默认选项，当需要由编排器来组织答案时它是正确选择。因为编排器要往返调用，所以每轮成本最高。
- **`handoff`** —— 当某个专业智能体应当独占答案时。之所以比 `tool` 更便宜也更快，正是因为没有人再重写专业智能体的回复。
- **`group-chat`** —— 当需要几种固定视角相互交锋后才得出结论时。成本随参与者数量增长。
- **`workflow:*`** —— 当工作形态可以事先确定时。没有模型、没有波动、毫秒级 —— 但也无法处理图未预见到的任何情况。

## 复现方式

```bash
./scripts/dev.sh                         # Python 技术栈，.env 中填真实 LLM 密钥
cd agents/python
uv run python -m evals.benchmark_modes --reps 2 --delay 8
```

结果会以带时间戳的 JSON 落到 `agents/python/evals/results/`，其中记录了提交号。这会花费真实费用 —— 上面那次运行约 0.50 美元。

## 相关内容

- [编排模式](concepts/06-orchestration-patterns.md) —— 每种模式*是什么*
- 报告与实际 —— 上述两个缺陷是如何被发现的
- [第 21 章 —— 综合演练](../tutorials/21-capstone-tour/) —— 每种模式在代码中的位置
