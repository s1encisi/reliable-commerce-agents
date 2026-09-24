# 更新日志

本项目所有值得注意的变更都记录在这里。

格式遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/spec/v2.0.0.html)。

条目由人工撰写，而非从提交记录生成。真正重要的是「使用或阅读本仓库的人感受到了什么变化」，
这是一个需要判断的决定 —— 下面若干条目会直说「这从来没正常工作过」，
而任何提交信息生成器都不会产出这种话。

发版由 `scripts/bump_version.py` 与 `.github/workflows/release.yml` 完成。见
[`docs/releasing.md`](docs/releasing.md)。

## [Unreleased]

Azure 前置工作。两个应用代码层面的阻塞项，任何一个都会让部署无法进行，而它们都与 Azure 服务无关。
两者都是契约变更，任何新增后端都会继承，因此它们在基础设施工作之前落地，而不是在过程中。

### 变更

- **前端不再需要知道其后端的地址。** `NEXT_PUBLIC_API_URL` 过去会在构建时被内联进客户端
  bundle，而 Container Apps 的 FQDN 要等到资源创建后才存在 —— 所以镜像不得不在部署后重建，
  这正是「一条命令完成部署」无法实现的原因。现在浏览器只调用自己的源，
  `web/src/app/api/[...path]/route.ts` 会把 `/api/*` 转发到 `ORCHESTRATOR_URL`，一个每请求
  读取的服务端变量。同一个镜像可以跑在任何环境，编排器无需公网入口，CORS 也不再需要。
  `NEXT_PUBLIC_API_URL` 作为直连的逃生口保留。

  有两件事只有真正跑起来的服务器才能发现。`next.config.ts` 里的 `rewrites()` 条目做不到这件事：
  Next 会在 `next build` 期间求值 `rewrites()` 并把目标写进 `routes-manifest.json`，那只是把
  构建时的问题换了个地方。另外，转发前**删除**浏览器的 `accept-encoding` 还不够，因为该头缺失时
  undici 会用自己的默认值顶替 —— 必须把它固定为 `identity`，否则编排器仍可能压缩 SSE 流。
  单测通过了，而真实服务器发的是 `gzip, deflate`。

  这也让一条已记录的约束作废了一半。`NEXT_DIST_DIR` 之所以存在，是因为第二个开发服务器若基于
  一份热的构建目录启动，就会沿用第一个服务烘焙进去的 API URL。现在构建产物不再编码任何与后端
  有关的信息。该故障形态仍会以 `ORCHESTRATOR_URL` 配置错误的形式出现，因此
  `assertFrontendTalksToOrchUrl` 保留。

### 修复

- **`AGENT_REGISTRY` 会静默降级，而不是报错。** 最初被记录为「硬编码 host:port」，
  实际问题更大。一个会做校验的解析器早就存在、也有测试，**但没有任何生产调用点使用它** ——
  四个调用点都在手工重新解析 JSON，其中三处把格式错误的值吞成了空注册表；空注册表能构建、
  能提供服务、能通过健康检查，却无法路由。现在只有一个校验器
  `shared.factory.parse_agent_registry`，它对格式错误的 JSON、空白 URL 或无 scheme 的 URL
  都会抛错。scheme 与 host 会被检查，端口不会 —— 托管端点没有端口，要求端口恰好会拒绝这个
  校验器为之服务的部署。作为
  [`docs/reported-vs-actual.md`](docs/reported-vs-actual.md) 的第 9 行加入。

- **三个既有测试缺陷**，每一个都对照过为绕过代理而构建的对照前端，因此都不是本次工作造成的。
  `chat-generative-ui` 与 `chat-shopping` 断言只渲染一张卡片，而应用对每个结果都会渲染一张；
  `chat-shopping` 的加购流程选中的是商品网格锚点，而那些卡片改为
  `onClick` + `router.push` 后锚点已不存在，于是它在一个不可能存在的元素上耗尽了整整 90 秒超时。
  三处现在都改为断言存在性，这也正是一致性门禁本就写明的标准。

## [1.3.0] - 2026-08-27

这一窗口内的一批缺陷，是靠运行软件而不是阅读代码才发现的。所有容器在整个过程中都报告健康。
这是本项目第四个靠运行而非阅读发现的缺陷，也是第一个健康检查主动劝阻人去查看的缺陷。

### 修复

- **`handoff` 模式耗时 100–200 秒、返回 19–25k 字符**，而 `tool` 模式对同一提示词约 11 秒、
  约 1,000 字符。方案中的假设 —— 累积流式文本的二次方增长 —— 经实测被否定：那些增量是真实的。
  真正的原因是起始智能体。handoff 过去以会调用工具的编排器作为种子，于是它既路由又作答，
  而不是把处理权交出去；5,403 次更新与 23,637 个字符全部来自 `orchestrator`，没有任何专业智能体
  发过言。现在从无工具的初筛智能体开始（1,374 字符，约 8 秒）。
- **`workflow:pre-purchase` 静默地只用了一半输入作答。** 四个执行器都跑了，回复却只有 48 个字符。
  本仓库早先的诊断归咎于综合步骤，那是错的，此处更正。综合步骤本身是忠实的 —— 它读取
  `sentiment` 与 `options`，而它自己的工具返回的是 `overall_sentiment` 与 `shipping_options`，
  因此四份贡献里总有两份缺失。测试桩编码了同样错误的契约，这正是测试通过的原因。
  现在输出 127 个字符，四份贡献齐全。
- **`HANDOFF_MAX_TURNS` 从未从环境变量读取。** 它被加进了 settings 记录与模式，却没加进加载器，
  所以设置它完全无效。已由一个测试守护：当某个设置只存在于一处而不存在于另一处时即失败。
- **过期的数据库卷会在无任何报错的情况下破坏搜索。** `scripts/dev.sh` 会探测过期的凭据，
  却不探测过期的 *schema*，因此一个早于全文检索迁移的卷会正常启动，然后在查询时报错。
- **evals 套件在没人改动任何东西的某天变红。** `get_sentiment_trend` 在
  `NOW()` 相对窗口内按自然月给评论分桶，而种子脚本把每条评论放在距种子时刻固定的天数偏移上。
  窗口内评论的集合是不变的；一个固定偏移落在哪个自然月却会变，所以同样的 15 条评论在录制当天
  分成 7 个桶、之后分成 5 个。回放哈希本就只擦除了月份*标签*便止步，于是 fixture 键悄然变成了
  墙钟日期的函数。已在离线状态重新生成键 —— 未产生任何 API 费用。
- **三个 E2E 测试对着被 mock 的 API 断言**，另一个断言的导航链接本就是被刻意移除的。
  已修复，并且把「它们此前掩盖了什么」记录下来，而不是悄悄改掉。

### 新增

- **聊天线程内的审批控件。** 暂停与恢复闭环此前已经真实存在，但唯一能解除暂停的控件位于
  `/runs` —— 因此触发暂停的用户，正看着一条在退货流程中途停下的消息，却不知道还有另一个页面的
  存在，也就无法采取行动。障碍在于没有任何流式客户端知道这次运行的 id；现在持久化之后、
  `[DONE]` 之前会发出 `event: run`。
- **在答案到达之前就出现的工具步骤。** 过去时间线步骤会被攒到最后一个文本分块之后才批量输出，
  于是时间线在答案写完时才出现 —— 恰好是它不再有用的时候。现在专业智能体宿主会在每个分块前
  先排空步骤，编排器也会实时转发它们。
- **本仓库自有的成本计数器。** Python 的 `get_meter()` 自遥测接入起就暴露在外却从未被调用过，
  因此本应用唯一真正知道的数字 —— 一次运行花了多少 —— 只以日志行的形式存在，而日志行无法用于
  告警。现在是 `ecommerce.llm.cost.usd`，旁边是按方向拆分的 token，属性中不含任何按用户隔离的
  信息。
- **一个会响应会话的输入区**（[#4](https://github.com/s1encisi/reliable-commerce-agents/issues/4)）——
  六个常驻可见的模式 chip 收拢为一个选择器，建议行改为从助手的最后一条消息推导
  （优先取它带类型的卡片载荷，其次取它的收尾提问），而不是每轮之后都重复同样四条预设提示。
  不涉及任何 LLM 调用。
- **公开的编排模式基准**（[`docs/orchestration-benchmark.md`](docs/orchestration-benchmark.md)）——
  按模式给出延迟、token、成本与响应长度，并附上提示词集合、日期、模型与提交号，
  因为不带条件的基准只是轶事。
- **五份架构决策记录**（[`docs/adr/`](docs/adr/)）—— A2A 优先于直连、不做 text-to-SQL、
  YAML 提示词组合、MAF 原生执行、双栈一致性对齐。每一份都写明什么情况下它是错的。
- **[`docs/reported-vs-actual.md`](docs/reported-vs-actual.md)** —— 八个案例，每次都显示上报的
  问题比实际问题更小，而且每一个都是靠运行而非阅读发现的。它曾是仓库里最可信的产物，
  却一直藏在 `.claude/` 里无人可见。

### 变更

- **`full` 评测任务不再按计划运行。** 它会消耗真实的 API key，而每周的定时任务会为一个没人要的
  结果按时计费 —— 2026-08-24 的定时运行失败了，就那样搁着没人看。现在只支持
  `workflow_dispatch`，该规则也写进了工作流里，让此后新增的任务继承它。

## [1.2.0] - 2026-08-26

这里面有两件事是靠运行软件而不是阅读代码发现的，这正在成为本项目的常态。编排模式的故障，
自镜像形成当前形态以来，一直在每一个容器镜像里被发布出去。

### 修复

- **五个编排模式中有三个在每一个 Docker 镜像里都是死的。**
  `workflow:pre-purchase`、`workflow:return-replace` 与 `group-chat` 返回
  「I apologize, but I encountered an issue processing your request」—— 无论提示词是什么，
  都是同样的 82 个字符，耗时不到 10 毫秒。Dockerfile 只复制了 `shared/`、`config/` 与
  `${AGENT_NAME}/`，别的什么都没复制，于是 `workflows/` 缺失，编排器工作流模式在进程内导入的
  四个专业智能体包也缺失。所有容器化部署都受影响，包括朴素的 `docker compose up`，
  而不只是已发布的镜像。
  没有任何东西发现它：E2E 是刻意不在 CI 中运行的，评测框架在进程内运行、那些包无论如何都在
  `sys.path` 上、与镜像内容无关，而镜像冒烟测试只导入 `<agent>.main` —— 这些导入都是懒加载的，
  发生在模式内部，所以模块能干净地导入，直到请求到来时才失败。
- **一次 PR 推送可能取消正在进行的镜像发布。** `workflow_dispatch` 没有定义 `tag_mode` 输入，
  于是并发分组的兜底逻辑把一次手动发布并入了与 PR 构建相同的桶。随后一次推送取消了一场已经推送了
  十个镜像中若干个的发布，留下半更新的标签。

### 新增

- **混合商品检索。** `search_products` 过去把查询拆成词，为每个词 AND 一个 `%word%` 的
  `ILIKE`，然后只按评分排序，因此「noise cancellation」永远匹配不上「noise cancelling」，
  而只要有一个词缺失结果集就会为空。现在改为 GIN 索引之上的加权生成列
  `tsvector`（name=A / brand=B / description=C），以 OR 连接并按 `ts_rank` 排序。
  `semantic_search` 也随之变为混合检索：向量臂与全文臂作为两个独立排序的 CTE，
  由 Reciprocal Rank Fusion 融合。已应用于原生工具与 `mcp-product`。
  **升级既有数据库：** `tsvector` 列随 `docker/postgres/init.sql` 发布，而 Postgres 只会在
  空数据目录上运行它 —— 要么执行 `./scripts/dev.sh --clean`，要么原地应用它，
  见 [故障排查](docs/troubleshooting.md#products-search_vector-does-not-exist)。
- **一条命令的演示：拉取镜像而不是构建。** `docker-compose.demo.yml` 加上
  `./scripts/dev.sh --demo`（PowerShell 上是 `-Demo`）把首次运行从大约十二分钟缩短到大约一分钟。
  在干净机器上实测：拉取全部十个镜像 37 秒，到整套服务健康 24 秒。`IMAGE_TAG` 可覆盖标签，
  用于测试 `:main` 或某个固定版本。
- **容器镜像发布到 GHCR，覆盖全部十个服务，并以测试套件为门禁。** 推送到 `main` 会发布
  `:main` 与 `:sha-<7>`；打版本标签会在完整重跑测试并人工批准后发布 `:vX.Y.Z` 与 `:latest`。
  镜像支持 `linux/amd64` 与 `linux/arm64`，因此 Apple Silicon 可以原生运行而不必跑在 QEMU 下。
- **一条发布流水线。** `.github/workflows/release.yml`、`scripts/bump_version.py`（带一个 CI
  用于阻断版本漂移的 `--check` 模式）、`CHANGELOG.md`，以及
  [`docs/releasing.md`](docs/releasing.md)。在此之前，一个 semver 标签会在不依赖任何测试任务的
  情况下发布镜像，所以在一个红色的提交上打标签也会发出去。
- **镜像仓库的保留策略。** `package-cleanup.yml` 每周运行，每个包保留最近 20 个版本，
  且从不触碰 `:latest`、`:main` 或任何 `:vX.Y.Z`。
- **`llms.txt`、`llms-full.txt` 与 `robots.txt`**，由站点构建所用的同一批页面生成。
  在 14 天窗口内，chatgpt.com 给本仓库带来的流量超过 Google 或 Bing 各自单独带来的量，
  而站点没有发布任何适配它的内容。
- **`.env.minimal`** —— 只有一个变量，现在是快速开始的默认方式，`.env.example`（210 行）
  降级为参考资料。[`docs/configuration.md`](docs/configuration.md) 记录了单个 `.env` 如何抵达
  容器、宿主机运行的 Python 与前端，这几者读取它的方式并不相同 —— 尤其要注意，容器根本不读它。
- **社区面** —— `SECURITY.md`、行为准则、issue 与 pull request 模板，以及 Discussions。
- **一个编排模式基准框架**（`evals/benchmark_modes.py`）。它驱动 HTTP API 而不是在进程内调用
  模式，因此一次运行会真实经过鉴权、护栏、事实核验与用量日志，而不是它们的副本。
  未接入 CI：它会消耗真实 token，且无法在 `LLM_PROVIDER=replay` 下运行。
- **一个 Playwright 录制脚本**用于演示片段（`web/e2e/demo-recording.spec.ts`），
  这样 UI 变更后片段可以重新录制，而不是慢慢失效。

### 变更

- **`build-images.yml` 不能再自行发起发布。** 它的 `push` 与标签触发器已移除；
  发布改由调用方通过 `workflow_call` 驱动，门禁也就跟着调用方走。
  镜像矩阵覆盖十个镜像而不是六个 —— `auth-server`、`mcp-product`、
  `mcp-inventory` 与 `frontend` 从未被 CI 构建过。
- **README 从 740 行缩减为 247 行。** 资深读者想看的内容 —— 事实核验、幂等性、人工参与、
  限流、追踪 —— 原本位于第 624 行开始的章节。没有任何内容被无去处地删除：
  [`docs/roadmap.md`](docs/roadmap.md) 与
  [`docs/demo-guide.md`](docs/demo-guide.md) 是新增的。
- **教程索引不再把已完成的章节描述为草稿。** 有三十四行写着「Draft」，而那些章节早已完成并在 CI
  中受门禁保护；那套词汇描述的是博客文章。该表格现在由磁盘上的实际内容生成，这也暴露了周边文字里
  四处不实说法。

## [1.1.0] - 2026-08-25

本次发布的主题，直白地说：**连续五次，上报的问题都比实际问题更小**，而每次的差异都是靠运行
某个东西而不是阅读它发现的。其中两个之所以被发现，仅仅是因为一道 CI 门禁刚被打开。

### 修复

- **追问能保留上下文了。** 专业智能体在任何来自浏览器的轮次上都收不到*任何*会话历史，
  且是确定性地复现。Web 客户端从不发送 `x-session-id`，于是上下文重建在到达数据库之前就短路了，
  而且没有任何日志。这件事在数周里被当成模型的不确定性，因为编排器有时把上下文内联进发给专业
  智能体的消息里、有时不内联。已修复，且重建查询现在限定在调用者自己的会话范围内。
- **语义检索真的能用了。** 它在 `LLM_PROVIDER=replay` 下是死的，因此没有任何一次 CI 运行真正
  跑过 pgvector —— 而在它之下还压着一个生产缺陷：IVFFlat 索引是在空表上创建的，所以它没有
  质心，在相似度 0.000 处返回不相关的商品，而精确扫描能在 0.420 处返回正确的那个。
- **促销真的会生效了。** `promotions.rules` 是无类型 JSONB，而种子脚本写入的键名与读取方读取的
  键名不同，于是组合优惠在每张购物车上都贡献 ¥0，买 X 赠 Y 直接崩溃，限时特卖静默地永不匹配。
  在任何环境下，都没有任何一次促销正确生效过。
- **文档站点可被索引了。** 全部 85 个页面共用同一条 meta description。现在每页都有自己的描述、
  关键词、`TechArticle` JSON-LD、`lastmod`、社交分享图，以及为全部 71 张图提供的可访问标题。

### 新增

- **工作流恢复。** 暂停 → 徽标 → 批准 → 恢复这一闭环是真实可用的：恢复会从 Postgres 检查点重建
  工作流，而不是把暂停的运行留在内存中，因此能挺过编排器重启；待处理行会在工作流执行前被认领，
  所以双击不会释放两笔退款。

## [1.0.0] - 2026-08-20

首个版本。一个多智能体电商平台，Python 后端与 Next.js 前端。

### 新增

- **六个经 A2A 通信的专业智能体** —— 商品发现、订单管理、定价与促销、
  评论情感分析、库存与履约，外加编排器这个统一入口。
- **实时可用的编排模式** —— 同一个问题可以由工具路由、处理权交接网状结构、两个工作流图或
  群聊圆桌来回答，按请求从输入区选择。图会依据真实 SSE 事件逐节点播放动画，
  「比较模式」会把一个提示词依次跑过多个模式并并排给出延迟。
- **智能体评测器** —— 覆盖全部六个专业智能体的评测集（precision@k、recall@k、答案忠实度、
  工具调用正确性）。`smoke` 任务使用已提交的回放 fixture 为每个 pull request 把关，
  因此不需要 API key、零成本。该框架驱动的是*生产*路径，所以护栏、清洗与人工参与门会被真实经过
  而不是绕过。
- **服务端事实核验** —— 模型的断言在答案发出之前会先对照 Postgres 校验。卡片块中的商品与订单 id
  会被核实确实存在，且携带所引用的价格。
- **提示词注入防护** —— 所有智能体的中间件栈中都有 `shared/guardrails/`，默认先观察。
- **带检查点恢复的人工参与** —— 工作流在其人工参与门上挂起，并从真实的 Postgres 检查点恢复。
- **涉及资金的动作用幂等性保护** —— 一张 `idempotency_keys` 表，加上退货、退款与结算上的
  `@idempotent` 装饰器。审批写入失败时**向安全侧失败**（fail closed）。
- **韧性与限流** —— 每次 A2A 调用都有带抖动退避的有界重试与按端点的熔断器，
  两条聊天路由上都有 Redis 滑动窗口限流器。
- **分布式追踪** —— 全程 OpenTelemetry，采用 GenAI 语义约定，配一个 Langfuse 接收端，
  并把 `trace_id` 关联进 `usage_logs`。跨度能跨 A2A 跳转正确嵌套。
- **MCP 数据访问层** —— `mcp-product` 与 `mcp-inventory` 作为 uv 工作区中独立、可单独发布的包，
  任何 MCP 客户端都能使用。
- **自托管 OAuth2 授权服务器** —— 可选的 `AUTH_MODE=oauth` 路径，令牌签发方就在本仓库内。
  RS256 配 JWKS 端点，以客户端凭据方式签发服务令牌来取代静态的 A2A 共享密钥，
  并把两个 MCP 服务器加固为 OAuth 2.1 资源服务器。
- **生成式 UI** —— 每个智能体响应都按其数据形状渲染：卡片、表格、图表、徽标。
  格式错误的载荷什么也不渲染，而不是回退成原始 JSON。
- **会话记忆与上下文持久化** —— `store_memory` / `recall_memories` 通过上下文提供器暴露给编排器。

[Unreleased]: https://github.com/s1encisi/reliable-commerce-agents/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/s1encisi/reliable-commerce-agents/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/s1encisi/reliable-commerce-agents/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/s1encisi/reliable-commerce-agents/releases/tag/v1.0.0
