# 智能体协作流程

可靠电商多智能体平台中五个多智能体协作场景的详细时序图。每个流程都展示了编排器如何识别意图、通过 A2A 协议路由到多个专业智能体，并综合出统一的回复。

---

## 流程 1：退货申请、管理员审批与结果核实

当前固定退货模式调用共享业务工具，顺序是资格检查、需要时等待管理员、执行前复核、创建申请与只读替代品建议。不能把工具调用成功理解成退款到账，也没有自动把退货金额存入余额或抵扣新订单的交易。

```mermaid
sequenceDiagram
    actor Customer as 客户
    actor Admin as 管理员
    participant UI as 前端
    participant API as 编排器 API
    participant WF as 固定退货工作流
    participant DB as PostgreSQL

    Customer->>UI: 提交明确订单及原因
    UI->>API: POST /api/chat（workflow:return-replace）
    API->>WF: 以可信客户身份运行
    WF->>DB: 核对订单归属和签收证据
    DB-->>WF: 当前资格与审批要求

    alt 需要审批
        WF->>DB: 保存检查点、审批请求和等待状态
        WF-->>API: AWAITING_APPROVAL + operation_id
        API-->>UI: 等待管理员，未创建退货
        Admin->>API: POST /api/orchestration/{run_id}/resume
        API->>WF: 恢复原申请人身份及绑定的审批决定
    end

    WF->>DB: 事务内再次检查规则与审批绑定
    alt 复核通过
        WF->>DB: 原子保存退货申请、订单状态和操作回执
        DB-->>WF: 持久 return_id
        WF-->>API: 申请已创建，非退款到账
    else 复核失败
        WF-->>API: 拒绝或需要核实，无新增退货
    end
    Customer->>API: GET /api/returns/operations/{operation_id}
    API->>DB: 按本人归属查询实际回执
    DB-->>API: 已持久结果或仍未知
    API-->>Customer: 明确状态与下一步
```

REST、普通工具也使用同一售后服务，但工具审批队列经 `/api/admin/hitl/requests/{id}/approve` 执行。两类入口都要求管理员决定、按原申请人身份做业务复核。后续商品、库存和价格建议是只读能力，不授予新的敏感写入权限。

可追踪实现：[工作流](../agents/python/workflows/return_replace.py)、[审批恢复接口](../agents/python/orchestrator/routes/orchestration.py)、[统一事务](../agents/python/shared/after_sales/service.py)。详见[售后架构](after-sales-architecture.md)。

---

## 流程 2：购买前调研

**用户**：「Sony WH-1000XM5 值得买吗？」

该调研流程会调用评论与情感分析做口碑分析、商品发现找替代品、定价与促销查当前优惠 —— 最终给用户一份完整的购买决策简报。

```mermaid
sequenceDiagram
    actor User
    participant FE as Next.js 前端
    participant ORCH as 编排器<br/>（FastAPI :8080）
    participant LLM as OpenAI / Azure OpenAI
    participant RS as 评论与情感分析<br/>（:8084）
    participant PD as 商品发现<br/>（:8081）
    participant PP as 定价与促销<br/>（:8083）
    participant DB as PostgreSQL

    User->>FE: 「Sony WH-1000XM5 值得买吗？」
    FE->>ORCH: POST /api/chat

    Note over ORCH: 意图：评论分析 +<br/>商品对比 + 定价

    ORCH->>LLM: Agent.run()
    LLM-->>ORCH: 工具调用：call_specialist_agent<br/>（review-sentiment，「分析 Sony WH-1000XM5 的评论」）

    rect rgb(15, 118, 110)
        Note over ORCH,RS: A2A 调用 1：评论与情感分析
        ORCH->>RS: POST /message:send
        RS->>LLM: Agent.run()
        LLM-->>RS: 工具：get_product_reviews(product_id)
        RS->>DB: SELECT reviews WHERE product_id<br/>ORDER BY helpful_count DESC
        DB-->>RS: 47 条评论，平均 4.6 星
        LLM-->>RS: 工具：analyze_sentiment(product_id)
        RS->>DB: 汇总评分，提取主题
        DB-->>RS: 按维度拆分的情感分布
        LLM-->>RS: 工具：get_sentiment_by_topic(product_id)
        RS->>DB: 从评论正文提取主题
        DB-->>RS: 主题：降噪（92% 正面）、<br/>佩戴舒适（88% 正面）、续航（95% 正面）、<br/>通话质量（61% 正面）
        LLM-->>RS: 工具：detect_fake_reviews(product_id)
        RS->>DB: 标记可疑模式<br/>（有用数低、文本雷同）
        DB-->>RS: 2 条被标记，45 条通过核验
        RS-->>ORCH: 4.6/5 星（45 条通过核验的评论）<br/>优点：降噪、续航、佩戴舒适<br/>不足：有风时通话质量<br/>真实性：96% 为真实评论
    end

    ORCH->>LLM: 处理评论与情感分析的响应，继续路由
    LLM-->>ORCH: 工具调用：call_specialist_agent<br/>（product-discovery，「寻找 Sony WH-1000XM5 的替代品」）

    rect rgb(13, 148, 136)
        Note over ORCH,PD: A2A 调用 2：商品发现
        ORCH->>PD: POST /message:send
        PD->>LLM: Agent.run()
        LLM-->>PD: 工具：find_similar_products(product_id)
        PD->>DB: 基于商品向量嵌入做<br/>pgvector 相似度检索
        DB-->>PD: Bose QC Ultra、Apple AirPods Max、<br/>Sennheiser Momentum 4
        LLM-->>PD: 工具：compare_products([sony, bose, sennheiser])
        PD->>DB: SELECT 3 款商品的规格参数
        DB-->>PD: 并排对比数据
        PD-->>ORCH: 3 款替代品及其对比：<br/>价格、评分、关键规格
    end

    ORCH->>LLM: 处理商品发现的响应，继续路由
    LLM-->>ORCH: 工具调用：call_specialist_agent<br/>（pricing-promotions，「Sony WH-1000XM5 当前的优惠」）

    rect rgb(217, 119, 6)
        Note over ORCH,PP: A2A 调用 3：定价与促销
        ORCH->>PP: POST /message:send
        PP->>LLM: Agent.run()
        LLM-->>PP: 工具：get_active_deals()
        PP->>DB: SELECT promotions WHERE<br/>category 含 Electronics
        DB-->>PP: 春季数码促销进行中
        LLM-->>PP: 工具：get_price_history(product_id)
        PP->>DB: SELECT price_history<br/>ORDER BY recorded_at DESC
        DB-->>PP: 当前：¥349，原价 ¥399，<br/>历史最低：¥299
        LLM-->>PP: 工具：get_loyalty_tier(user_email)
        PP->>DB: SELECT loyalty_tier FROM users
        DB-->>PP: 黄金等级 = 9 折
        PP-->>ORCH: 当前：¥349（较官方价低 12%）<br/>黄金会员：再享 9 折<br/>到手价：约 ¥314<br/>历史最低为 ¥299（大促期间）
    end

    ORCH->>LLM: 综合出购买决策简报
    Note over LLM: 把评论情感、<br/>替代品对比、<br/>定价合并为一份<br/>明确的「买还是等」建议

    ORCH->>DB: 持久化会话
    ORCH-->>FE: 调研摘要
    FE-->>User: 「Sony WH-1000XM5 口碑很好（4.6/5）……<br/>叠加您的黄金会员折扣，当前优惠后约 ¥314……<br/>以下是它与替代品的对比……」
```

---

## 流程 3：我的订单到哪了

**用户**：「我的订单到哪了？」

一个聚焦的两智能体流程：订单管理取出订单详情与物流轨迹，随后库存与履约查询承运商的实时状态与预计送达时间。

```mermaid
sequenceDiagram
    actor User
    participant FE as Next.js 前端
    participant ORCH as 编排器<br/>（FastAPI :8080）
    participant LLM as OpenAI / Azure OpenAI
    participant OM as 订单管理<br/>（:8082）
    participant IF as 库存与履约<br/>（:8085）
    participant DB as PostgreSQL

    User->>FE: 「我的订单到哪了？」
    FE->>ORCH: POST /api/chat

    Note over ORCH: 意图：订单物流查询<br/>ECommerceContextProvider 把<br/>近期订单注入上下文

    ORCH->>LLM: Agent.run()
    Note over LLM: 上下文中已包含<br/>近期订单 —— 选中<br/>最近一笔进行中的订单
    LLM-->>ORCH: 工具调用：call_specialist_agent<br/>（order-management，「查询用户最近一笔订单的物流」）

    rect rgb(15, 118, 110)
        Note over ORCH,OM: A2A 调用 1：订单管理
        ORCH->>OM: POST /message:send
        OM->>LLM: Agent.run()
        LLM-->>OM: 工具：get_user_orders()
        OM->>DB: SELECT orders WHERE user_id<br/>ORDER BY created_at DESC LIMIT 5
        DB-->>OM: 最近一笔：订单号 #a1b2c3<br/>状态：已发货，合计：¥149.99
        LLM-->>OM: 工具：get_order_details(order_id)
        OM->>DB: SELECT 订单 + 明细 + 状态历史
        DB-->>OM: 2 件商品，2 天前发货<br/>承运商：顺丰速运，运单号：SF1234567890
        LLM-->>OM: 工具：get_order_tracking(order_id)
        OM->>DB: SELECT order_status_history<br/>ORDER BY timestamp
        DB-->>OM: 已下单 -> 已确认 -> 已发货<br/>最新位置：杭州转运中心
        OM-->>ORCH: 订单 #a1b2c3：已由顺丰速运发出<br/>运单号：SF1234567890<br/>最新轨迹：杭州转运中心（2 天前）
    end

    ORCH->>LLM: 处理订单管理的响应，获取预计送达时间
    LLM-->>ORCH: 工具调用：call_specialist_agent<br/>（inventory-fulfillment，「运单号 SF1234567890 的承运状态与预计送达时间」）

    rect rgb(100, 116, 139)
        Note over ORCH,IF: A2A 调用 2：库存与履约
        ORCH->>IF: POST /message:send
        IF->>LLM: Agent.run()
        LLM-->>IF: 工具：get_tracking_status(tracking_number)
        IF->>DB: SELECT order_status_history<br/>+ 承运商信息
        DB-->>IF: 运输中，最新扫描记录在杭州转运中心
        LLM-->>IF: 工具：estimate_shipping(product_id, address)
        IF->>DB: SELECT 按时效与地区查询运费
        DB-->>IF: 顺丰速运：剩余 2–3 天
        IF-->>ORCH: 已到达杭州转运中心，运输中<br/>预计送达：2–3 个工作日<br/>进度正常，未检测到延误
    end

    ORCH->>LLM: 综合物流回复
    ORCH->>DB: 持久化会话
    ORCH-->>FE: 物流摘要
    FE-->>User: 「您的订单 #a1b2c3（¥149.99）已在路上！<br/>目前由顺丰速运承运，已到达杭州转运中心。<br/>预计送达：4 月 7–8 日。<br/>运单号：SF1234567890」
```

---

## 流程 4：缺货提醒

**用户**：「Dyson V15 缺货了，什么时候能买到？」

库存与履约检查库存量与补货计划，如果等待时间过长，商品发现会找出有现货的替代品。

```mermaid
sequenceDiagram
    actor User
    participant FE as Next.js 前端
    participant ORCH as 编排器<br/>（FastAPI :8080）
    participant LLM as OpenAI / Azure OpenAI
    participant IF as 库存与履约<br/>（:8085）
    participant PD as 商品发现<br/>（:8081）
    participant DB as PostgreSQL

    User->>FE: 「Dyson V15 缺货了，什么时候能买到？」
    FE->>ORCH: POST /api/chat

    Note over ORCH: 意图：库存查询 +<br/>补货计划 + 替代品

    ORCH->>LLM: Agent.run()
    LLM-->>ORCH: 工具调用：call_specialist_agent<br/>（inventory-fulfillment，「查询 Dyson V15 的库存与补货计划」）

    rect rgb(100, 116, 139)
        Note over ORCH,IF: A2A 调用 1：库存与履约
        ORCH->>IF: POST /message:send
        IF->>LLM: Agent.run()
        LLM-->>IF: 工具：check_stock(product_id)
        IF->>DB: SELECT SUM(quantity)<br/>FROM warehouse_inventory<br/>WHERE product_id = $1
        DB-->>IF: 总库存：0 件
        LLM-->>IF: 工具：get_warehouse_availability(product_id)
        IF->>DB: SELECT warehouse_inventory wi<br/>JOIN warehouses w<br/>WHERE product_id = $1
        DB-->>IF: 华东：0，华中：0，华北：0
        LLM-->>IF: 工具：get_restock_schedule(product_id)
        IF->>DB: SELECT restock_schedule<br/>WHERE product_id = $1<br/>AND expected_date > NOW()
        DB-->>IF: 华中仓：50 件<br/>预计到货：2026 年 4 月 12 日
        LLM-->>IF: 工具：place_backorder(product_id)
        Note over IF: 提供缺货登记选项
        IF-->>ORCH: 所有仓库均缺货<br/>补货：华中仓约 50 件<br/>预计到货：4 月 12 日<br/>可登记缺货提醒
    end

    ORCH->>LLM: 处理库存与履约的响应，寻找替代品
    LLM-->>ORCH: 工具调用：call_specialist_agent<br/>（product-discovery，「寻找 Dyson V15 吸尘器的有货替代品」）

    rect rgb(13, 148, 136)
        Note over ORCH,PD: A2A 调用 2：商品发现
        ORCH->>PD: POST /message:send
        PD->>LLM: Agent.run()
        LLM-->>PD: 工具：find_similar_products(dyson_v15_id)
        PD->>DB: 基于 Dyson V15 的向量嵌入<br/>做 pgvector 相似度检索
        DB-->>PD: 按相似度排序的同类吸尘器
        LLM-->>PD: 工具：search_products("cordless vacuum", min_rating=4.0)
        PD->>DB: SELECT products WHERE category<br/>AND rating >= 4.0 AND is_active
        DB-->>PD: 5 款匹配的吸尘器
        Note over PD: 智能体借助共享库存工具<br/>交叉核对库存
        LLM-->>PD: 工具：check_stock(alternative_ids)
        PD->>DB: 逐个替代品查询 warehouse_inventory
        DB-->>PD: 3 款替代品有现货
        PD-->>ORCH: 3 款有现货的替代品：<br/>Shark Stratos（¥299，4.5 星）<br/>Samsung Jet 90（¥349，4.3 星）<br/>LG CordZero（¥279，4.4 星）
    end

    ORCH->>LLM: 综合缺货情况回复
    ORCH->>DB: 持久化会话
    ORCH-->>FE: 缺货提醒 + 替代品
    FE-->>User: 「Dyson V15 目前缺货。<br/>预计补货：4 月 12 日（约 8 天）。<br/>我可以为您登记缺货提醒。<br/><br/>同时，以下几款有现货的同类商品供您参考：<br/>1. Shark Stratos - ¥299（4.5 星）……」
```

---

## 流程 5：批量采购优化

**用户**：「我要给团队买耳机和音箱，各 5 套」

商品发现筛选候选，定价计算批量折扣与可用优惠券，库存确认所请求数量在各仓库的实际可发情况。

```mermaid
sequenceDiagram
    actor User
    participant FE as Next.js 前端
    participant ORCH as 编排器<br/>（FastAPI :8080）
    participant LLM as OpenAI / Azure OpenAI
    participant PD as 商品发现<br/>（:8081）
    participant PP as 定价与促销<br/>（:8083）
    participant IF as 库存与履约<br/>（:8085）
    participant DB as PostgreSQL

    User->>FE: 「我要给团队买耳机和音箱，各 5 套」
    FE->>ORCH: POST /api/chat

    Note over ORCH: 意图：商品检索 +<br/>批量定价 + 按数量核库存

    ORCH->>LLM: Agent.run()
    LLM-->>ORCH: 工具调用：call_specialist_agent<br/>（product-discovery，「筛选适合团队/办公场景的耳机与音箱」）

    rect rgb(13, 148, 136)
        Note over ORCH,PD: A2A 调用 1：商品发现
        ORCH->>PD: POST /message:send
        PD->>LLM: Agent.run()
        LLM-->>PD: 工具：search_products("office headphones", category="Electronics", sort_by="rating")
        PD->>DB: SELECT products WHERE category='Electronics'<br/>AND name ILIKE '%headphone%'<br/>ORDER BY rating DESC
        DB-->>PD: 评分最高的耳机
        LLM-->>PD: 工具：search_products("bluetooth speaker office", category="Electronics", sort_by="rating")
        PD->>DB: SELECT products WHERE category='Electronics'<br/>AND name ILIKE '%speaker%'
        DB-->>PD: 评分最高的音箱
        LLM-->>PD: 工具：compare_products([headphone_ids])
        PD->>DB: SELECT 用于对比的规格参数
        DB-->>PD: 并排规格对比
        PD-->>ORCH: 推荐组合：<br/>耳机：Sony WH-1000XM5（¥349）<br/>或 JBL Tune 770NC（¥99）<br/>音箱：JBL Charge 5（¥179）<br/>或 Bose SoundLink Flex（¥149）
    end

    ORCH->>LLM: 处理商品发现的响应，核对定价
    LLM-->>ORCH: 工具调用：call_specialist_agent<br/>（pricing-promotions，「5 套耳机 + 音箱的批量价格」）

    rect rgb(217, 119, 6)
        Note over ORCH,PP: A2A 调用 2：定价与促销
        ORCH->>PP: POST /message:send
        PP->>LLM: Agent.run()
        LLM-->>PP: 工具：check_bundle_eligibility(product_ids)
        PP->>DB: SELECT promotions WHERE type='bundle'<br/>AND rules 匹配商品分类
        DB-->>PP: 「数码组合购」促销进行中<br/>满 3 件：9 折
        LLM-->>PP: 工具：get_active_deals()
        PP->>DB: SELECT coupons WHERE is_active=TRUE<br/>AND applicable_categories 含 'Electronics'
        DB-->>PP: 优惠券 TEAM2026：满 ¥500 享 92 折
        LLM-->>PP: 工具：optimize_cart(items, quantities)
        PP->>DB: 计算最优价格组合
        DB-->>PP: 最优定价已算出
        LLM-->>PP: 工具：get_loyalty_tier(user_email)
        PP->>DB: SELECT loyalty_tier
        DB-->>PP: 白银等级 = 95 折
        PP-->>ORCH: 方案 A（高配）：5 套 Sony + JBL Charge = ¥2,640<br/>  组合购：-10%，TEAM2026：-8%，白银：-5%<br/>  到手：约 ¥2,030（省 ¥610）<br/>方案 B（性价比）：5 套 JBL Tune + Bose Flex = ¥1,240<br/>  组合购：-10%，TEAM2026：-8%，白银：-5%<br/>  到手：约 ¥954（省 ¥286）
    end

    ORCH->>LLM: 处理定价与促销的响应，核对库存
    LLM-->>ORCH: 工具调用：call_specialist_agent<br/>（inventory-fulfillment，「检查每个商品是否有 5 件可发」）

    rect rgb(100, 116, 139)
        Note over ORCH,IF: A2A 调用 3：库存与履约
        ORCH->>IF: POST /message:send
        IF->>LLM: Agent.run()
        LLM-->>IF: 工具：get_warehouse_availability(product_ids)
        IF->>DB: SELECT warehouse_inventory<br/>逐商品查询所有仓库
        DB-->>IF: 各仓库各商品的库存量
        LLM-->>IF: 工具：calculate_fulfillment_plan(items, quantities)
        IF->>DB: 跨仓库优化发货来源<br/>以最小化运费
        DB-->>IF: 履约方案已算出
        LLM-->>IF: 工具：estimate_shipping(plan)
        IF->>DB: SELECT 该方案的运费
        DB-->>IF: 运费估算
        IF-->>ORCH: 方案 A：Sony 有现货（共 12 件），JBL Charge 有现货（8 件）<br/>  由华中仓发货，标准配送：¥0（白银/黄金会员满 ¥500 免运费）<br/>方案 B：JBL Tune 有现货（15 件），Bose Flex 仅 3 件<br/>  2 件需登记缺货提醒，4 月 15 日补货<br/>  建议：先发 3 件，补货后再发 2 件
    end

    ORCH->>LLM: 综合批量采购建议
    Note over LLM: 把商品方案、<br/>叠加折扣后的最优价格、<br/>库存状态与履约方案<br/>整合为可执行的建议

    ORCH->>DB: 持久化会话
    ORCH-->>FE: 批量采购简报
    FE-->>User: 「为您的 5 人团队准备了两套组合方案：<br/><br/>高配（全部有现货，可立即发货）：<br/>5 套 Sony WH-1000XM5 + 5 套 JBL Charge 5<br/>合计：¥2,030（原价 ¥2,640 —— 省 ¥610）<br/>优惠：组合购 10% + TEAM2026 8% + 白银 5%<br/><br/>性价比（部分缺货）：<br/>5 套 JBL Tune 770NC + 5 套 Bose SoundLink Flex<br/>合计：¥954（原价 ¥1,240 —— 省 ¥286）<br/>注意：2 台 Bose 音箱需等 4 月 15 日补货<br/><br/>两套均由华中仓免运费发货。需要下单吗？」
```

---

## 流程小结

| 流程 | 涉及智能体 | 关键模式 |
|------|----------------|-------------|
| **退货与换购** | 固定退货工作流 + 共享业务服务 | 资格、必要审批、复核与申请回执；后续建议是只读，不代表退款到账 |
| **购买前调研** | 评论与情感分析 -> 商品发现 -> 定价 | 并行调研：汇集口碑、替代品与优惠 |
| **我的订单到哪了** | 订单管理 -> 库存与履约 | 聚焦式：先取订单数据，再取承运与送达状态 |
| **缺货提醒** | 库存与履约 -> 商品发现 | 兜底方案：先查库存，不可得时给出替代品 |
| **批量采购** | 商品发现 -> 定价 -> 库存 | 完整流水线：筛选、价格优化、履约核对 |

### 各流程共有的模式

- **上下文注入**：每个智能体在处理前都会通过 `ECommerceContextProvider` 拿到用户画像与近期订单。这让个性化（会员等级、购买历史）成为可能，而无需用户重复说明。
- **共享工具**：智能体可以调用主领域之外的工具。商品发现会调用 `check_stock()`（共享库存工具）以避免推荐缺货商品，从而减少不必要的 A2A 往返。
- **顺序 A2A**：编排器一次只调用一个专业智能体，每次响应都会影响下一次调用的消息。这是刻意的 —— 后面的智能体需要前面结果的上下文（例如定价需要先知道推荐了哪些商品）。
- **LLM 综合**：编排器的 LLM 不只是拼接专业智能体的回复，而是把它们综合成一条自然、连贯、结构清晰且带明确下一步动作的消息。
- **身份传递**：用户身份通过每次 A2A 调用上的 `X-User-Email` 与 `X-User-Role` 请求头传递，随后写入 ContextVars。每个工具查询都按用户过滤 —— 客户之间不存在数据泄露。

---

## 相关内容

- [`docs/architecture.md`](architecture.md) —— 系统总览、A2A 协议、编排器路由
- [`docs/maf-best-practices.md`](maf-best-practices.md) —— 编排模式（顺序、并发、处理权交接、人工参与）
- [`docs/adding-an-agent.md`](adding-an-agent.md) —— 如何把新的专业智能体加入路由表
- [`docs/mcp-integration.md`](mcp-integration.md) —— 把 MCP 作为专业智能体工具的另一种数据层
- [项目 README](../README.md)
