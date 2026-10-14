# API 参考

编排器通过 FastAPI 暴露聊天、售后、任务、会话上下文和管理 API。本文列出当前核心合同与常用接口；完整方法、请求模型和响应结构由运行服务的 OpenAPI 提供。

## 路由总览

本页聚焦主使用路径。已根据当前路由源码生成 OpenAPI 核对方法与路径；完整契约以运行中编排器的 `/openapi.json` 和 `/docs` 为准。隔离开发演示的 API 是 `http://localhost:8180`；普通开发默认 8080，容器展示通过前端同源 `/api/*` 访问。

```mermaid
flowchart LR
    Browser[浏览器或 API 客户端] --> Public[公开认证与模式目录]
    Browser --> Optional[可匿名的商品与购物聊天]
    Browser --> User[已认证用户]
    User --> Returns[本人订单申请与操作回执]
    User --> Tasks[本人只读任务：规划、执行、验收、恢复]
    User --> Context[本人会话目标与约束修订]
    User --> Admin{管理员角色}
    Admin --> ToolApproval[工具审批：批准或拒绝]
    Admin --> WorkflowApproval[工作流审批：resume]
    ToolApproval --> Business[按原申请人身份复核并执行]
    WorkflowApproval --> Business
    Business --> Receipt[数据库业务回执]
```

需要审批的退货不能由申请人自行批准；客户只能发起及查询。管理员作出决定后，执行仍使用原申请人的可信身份和订单归属。角色限制以服务端为准，隐藏按钮不能代替权限检查。

## 售后、审批与运行接口

| 方法与路径 | 权限 | 关键契约 |
| --- | --- | --- |
| `POST /api/orders/{order_id}/return` | 已认证用户，本人订单 | `{reason, refund_method}`；可用 UUID `Idempotency-Key` 固定请求意图。待审批返回 409 与 `AWAITING_APPROVAL`、操作/审批标识，不表示已创建退货 |
| `GET /api/returns/operations/{operation_id}` | 原申请人 | 查询持久结果；其他用户或不存在的操作返回 404 |
| `GET /api/admin/hitl/requests` | 管理员 | 列出审批队列，可按 status 过滤 |
| `POST /api/admin/hitl/requests/{request_id}/approve` | 管理员 | 可带 `{note}`；返回审批决定及 `execution_result`，批准不保证业务复核成功 |
| `POST /api/admin/hitl/requests/{request_id}/deny` | 管理员 | 可带 `{note}`；拒绝并保存决定 |
| `POST /api/orchestration/{run_id}/resume` | 管理员 | `{approved: true/false}`；恢复已保存检查点。中断重试须保留首次决定和审批人 |
| `GET /api/runs` | 已认证用户，按归属过滤 | 查看运行记录 |
| `GET /api/runs/{run_id}/checkpoints` | 归属用户或管理员 | 查看检查点和审批摘要；查看权限不等于审批权限 |
| `GET /api/returns/{return_id}/fulfillment` | 原申请人 | 读取可选本地履约模拟状态；`PENDING` 与供应商已受理不同，其他用户返回 404 |

`SUCCEEDED` 代表此退货申请的本地业务结果，不代表退款到账。`UNKNOWN` 应查询同一操作标识；不能因网络错误直接换新标识重复申请。详见[售后架构](after-sales-architecture.md)与[履约模拟](fulfillment-simulation.md)。

## 聊天与模式接口

| 方法与路径 | 权限 | 行为 |
| --- | --- | --- |
| `POST /api/chat` | 可匿名访问公开购物能力；个人业务仍校验身份 | 阻塞响应，支持 mode |
| `POST /api/chat/stream` | 同上 | SSE 事件流；业务结果由最终回执判断 |
| `GET /api/orchestration/modes` | 公开 | 六种注册模式及能力描述 |
| `GET /api/orchestration/modes/{name}/graph` | 公开 | 固定图返回 Mermaid；动态工具路由可能返回 null |
| `POST /api/orchestration/compare` | 已认证用户 | `{message, modes}`，最多比较五个选定模式；这不是“总共只有五种模式” |

六种模式为 `tool`、`decision-router`、`handoff`、`workflow:pre-purchase`、`workflow:return-replace`、`group-chat`。决策路由策略默认 off。请求真实模型模式可能产生调用，离线演示不能保证任意自由文本都有回放夹具。

## 任务、约束与能力接口

| 方法与路径 | 权限 | 行为 |
| --- | --- | --- |
| `POST /api/tasks` | 已认证用户 | 创建 `{goal, constraints, requirements}` |
| `GET /api/tasks/{task_id}` | 本人任务 | 读取状态、计划和证据 |
| `PATCH /api/tasks/{task_id}` | 本人任务 | 携带 revision 修订目标和条件，使旧计划/结果失效 |
| `POST /api/tasks/{task_id}/plan` | 本人任务 | 携带 revision 显式规划 |
| `POST /api/tasks/{task_id}/next` | 本人任务 | 携带 revision 执行下一步 |
| `POST /api/tasks/{task_id}/recover` | 本人任务 | 携带 revision 恢复中断的规划或只读执行 |
| `POST /api/tasks/{task_id}/confirm` | 本人任务 | `{revision, confirmed:true}`，确认待核实的自然语言目标 |
| `POST /api/memories/{memory_id}/confirm` | 记忆归属用户 | 显式确认记忆来源 |
| `GET /api/conversations/{conversation_id}/context` | 本人会话 | 当前目标、约束、来源及 revision |
| `PATCH /api/conversations/{conversation_id}/context` | 本人会话 | 携带 revision，以及实际修改的 goal 或 constraints；新约束列表取代旧列表 |
| `GET /api/runtime/capabilities` | 已认证用户 | 非敏感能力标志、离线固定示例与开关；不返回密钥或连接串 |

409 表示版本或状态已变化，应重新读取并由用户确认，不自动覆盖。任务步骤的结构化验收不等于自由文本目标已被证明，`needs_review` 需要本人检查后确认。详见[任务工作台](task-workspace.md)。

## 认证

所有需要认证的端点都必须在 `Authorization` 请求头中携带 `Bearer` 令牌：

```
Authorization: Bearer <access_token>
```

默认本地认证模式使用 `JWT_SECRET` 签名的 JWT（PyJWT + bcrypt）；配置 OAuth 模式时使用对应的 RS256 校验路径。下列登录示例展示本地模式。访问令牌包含 `sub`（邮箱）、`role`、`user_id` 和 `type: "access"`；刷新令牌包含 `sub` 和 `type: "refresh"`。

管理员端点还会额外校验 `role == "admin"`，校验失败时返回 `403`。

---

## 认证（公开）

### POST /api/auth/signup

创建新的用户账号并返回令牌。

| 字段 | 取值 |
|-------|-------|
| 认证 | 可匿名访问公开购物能力；个人数据与业务工具仍校验身份 |

**请求体**

```json
{
  "email": "alice.johnson@gmail.com",
  "password": "securepassword",
  "name": "Alice Johnson"
}
```

**响应** `200`

```json
{
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "alice.johnson@gmail.com",
    "name": "Alice Johnson",
    "role": "customer",
    "loyalty_tier": "bronze",
    "total_spend": 0.0
  }
}
```

**错误**
- `409` 邮箱已被注册

---

### POST /api/auth/login

认证已有用户并返回令牌。

| 字段 | 取值 |
|-------|-------|
| 认证 | 可匿名访问公开购物能力；个人数据与业务工具仍校验身份 |

**请求体**

```json
{
  "email": "alice.johnson@gmail.com",
  "password": "securepassword"
}
```

**响应** `200`

```json
{
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "alice.johnson@gmail.com",
    "name": "Alice Johnson",
    "role": "customer",
    "loyalty_tier": "gold",
    "total_spend": 4200.0
  }
}
```

**错误**
- `401` 邮箱或密码无效
- `403` 账号已被停用

---

### POST /api/auth/refresh

用刷新令牌换取新的访问令牌。

| 字段 | 取值 |
|-------|-------|
| 认证 | 可匿名访问公开购物能力；个人数据与业务工具仍校验身份 |

**请求体**

```json
{
  "refresh_token": "eyJhbGciOi..."
}
```

**响应** `200`

```json
{
  "access_token": "eyJhbGciOi..."
}
```

**错误**
- `401` 刷新令牌已过期 / 无效 / 类型错误
- `403` 账号已被停用

---

## 聊天

### POST /api/chat

向编排器智能体发送一条消息。编排器会按需路由到专业智能体（specialist agent），并返回汇总后的响应。

| 字段 | 取值 |
|-------|-------|
| 认证 | 可匿名访问公开购物能力；个人数据与业务工具仍校验身份 |

**请求体**

```json
{
  "message": "What are the best noise-cancelling headphones under $350?",
  "conversation_id": null
}
```

`conversation_id` 为可选字段。省略或传入 `null` 即开始一个新会话；传入已有的 ID 则继续该会话（会加载最近 50 条消息作为上下文）。

**响应** `200`

```json
{
  "response": "I found several great options for noise-cancelling headphones under $350...",
  "conversation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "agents_involved": ["orchestrator", "product-discovery", "pricing-promotions"]
}
```

**错误**
- `401` 令牌缺失或无效
- `404` 会话不存在（当提供了 `conversation_id` 但它不属于当前用户时）

---

## 会话

### GET /api/conversations

列出已认证用户的会话，按最近活动时间排序。

| 字段 | 取值 |
|-------|-------|
| 认证  | 需要 JWT |

**响应** `200`

```json
[
  {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "title": "What are the best noise-cancelling headphones under $350?",
    "message_count": 4,
    "created_at": "2026-04-01T10:30:00+00:00",
    "last_message_at": "2026-04-01T10:35:22+00:00"
  }
]
```

最多返回 50 个活跃会话，已软删除的会话不计入其中。

---

### GET /api/conversations/{conversation_id}

获取单个会话及其完整的消息历史。

| 字段 | 取值 |
|-------|-------|
| 认证  | 需要 JWT |

**响应** `200`

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "title": "What are the best noise-cancelling headphones under $350?",
  "created_at": "2026-04-01T10:30:00+00:00",
  "last_message_at": "2026-04-01T10:35:22+00:00",
  "messages": [
    {
      "id": "msg-uuid-1",
      "role": "user",
      "content": "What are the best noise-cancelling headphones under $350?",
      "agent_name": null,
      "agents_involved": [],
      "metadata": {},
      "tokens_in": 0,
      "tokens_out": 0,
      "created_at": "2026-04-01T10:30:00+00:00"
    },
    {
      "id": "msg-uuid-2",
      "role": "assistant",
      "content": "I found several great options...",
      "agent_name": "orchestrator",
      "agents_involved": ["orchestrator", "product-discovery"],
      "metadata": {},
      "tokens_in": 150,
      "tokens_out": 320,
      "created_at": "2026-04-01T10:30:05+00:00"
    }
  ]
}
```

**错误**
- `404` 会话不存在或不属于当前用户

---

### DELETE /api/conversations/{conversation_id}

软删除会话（将 `is_active` 置为 `FALSE`），消息仍保留在数据库中。

| 字段 | 取值 |
|-------|-------|
| 认证  | 需要 JWT |

**响应** `200`

```json
{
  "status": "deleted"
}
```

**错误**
- `404` 会话不存在或不属于当前用户

---

## 商品

### GET /api/products

浏览和搜索商品目录，支持筛选与排序。

| 字段 | 取值 |
|-------|-------|
| 认证 | 可匿名访问公开购物能力；个人数据与业务工具仍校验身份 |

**查询参数**

| 参数        | 类型    | 默认值    | 说明 |
|-------------|---------|-----------|-------------|
| `category`  | string  | -         | 按分类筛选（Electronics、Clothing、Home、Sports、Books） |
| `min_price` | float   | -         | 最低价格筛选 |
| `max_price` | float   | -         | 最高价格筛选 |
| `search`    | string  | -         | 对商品名称与描述执行 ILIKE 搜索 |
| `sort`      | string  | `rating`  | 排序方式：`rating`、`price_asc`、`price_desc`、`newest`、`name` |
| `limit`     | int     | `50`      | 每页数量 |
| `offset`    | int     | `0`       | 分页偏移量 |

**响应** `200`

```json
{
  "products": [
    {
      "id": "prod-uuid-1",
      "name": "Sony WH-1000XM5",
      "description": "Premium wireless noise-cancelling headphones with 30-hour battery...",
      "category": "Electronics",
      "brand": "Sony",
      "price": 299.99,
      "original_price": 349.99,
      "image_url": "/images/products/sony-wh1000xm5.jpg",
      "rating": 4.7,
      "review_count": 12
    }
  ],
  "total": 50,
  "categories": ["Books", "Clothing", "Electronics", "Home", "Sports"]
}
```

在列表视图中，商品描述会被截断为 200 个字符。

---

### GET /api/products/{product_id}

获取商品的完整详情，包括规格参数、库存水平、评论以及评分分布。

| 字段 | 取值 |
|-------|-------|
| 认证 | 可匿名访问公开购物能力；个人数据与业务工具仍校验身份 |

**响应** `200`

```json
{
  "id": "prod-uuid-1",
  "name": "Sony WH-1000XM5",
  "description": "Premium wireless noise-cancelling headphones with 30-hour battery life...",
  "category": "Electronics",
  "brand": "Sony",
  "price": 299.99,
  "original_price": 349.99,
  "image_url": "/images/products/sony-wh1000xm5.jpg",
  "rating": 4.7,
  "review_count": 12,
  "specs": {
    "type": "Over-ear",
    "battery": "30 hours",
    "noise_cancelling": true,
    "weight": "250g",
    "connectivity": "Bluetooth 5.2"
  },
  "in_stock": true,
  "total_stock": 145,
  "warehouses": [
    { "name": "East", "region": "east", "quantity": 50 },
    { "name": "Central", "region": "central", "quantity": 65 },
    { "name": "West", "region": "west", "quantity": 30 }
  ],
  "reviews": [
    {
      "id": "review-uuid-1",
      "rating": 5,
      "title": "Best headphones I've owned",
      "body": "The noise cancellation is incredible...",
      "verified": true,
      "reviewer": "Alice Johnson",
      "date": "2026-03-15T14:20:00+00:00"
    }
  ],
  "rating_distribution": {
    "1": 1,
    "2": 0,
    "3": 2,
    "4": 3,
    "5": 6
  }
}
```

**错误**
- `404` 商品不存在

---

## 订单

### GET /api/orders

列出已认证用户的订单，可按状态筛选。

| 字段 | 取值 |
|-------|-------|
| 认证  | 需要 JWT |

**查询参数**

| 参数      | 类型   | 默认值  | 说明 |
|-----------|--------|---------|-------------|
| `status`  | string | -       | 按状态筛选：placed、confirmed、shipped、out_for_delivery、delivered、cancelled、returned |
| `limit`   | int    | `20`    | 每页数量 |
| `offset`  | int    | `0`     | 分页偏移量 |

**响应** `200`

```json
{
  "orders": [
    {
      "id": "order-uuid-1",
      "status": "delivered",
      "total": 349.98,
      "carrier": "Express",
      "tracking": "EXP-12345-US",
      "item_count": 2,
      "date": "2026-03-20T09:15:00+00:00"
    }
  ],
  "total": 8
}
```

---

### GET /api/orders/{order_id}

获取订单的完整详情，包括订单项、状态历史、收货地址以及退货信息。

| 字段 | 取值 |
|-------|-------|
| 认证  | 需要 JWT |

**响应** `200`

```json
{
  "id": "order-uuid-1",
  "status": "delivered",
  "total": 349.98,
  "shipping_address": {
    "street": "123 Main St",
    "city": "New York",
    "state": "NY",
    "zip": "10001",
    "country": "US"
  },
  "carrier": "Express",
  "tracking": "EXP-12345-US",
  "coupon": "SAVE10",
  "discount": 35.0,
  "date": "2026-03-20T09:15:00+00:00",
  "items": [
    {
      "product_id": "prod-uuid-1",
      "name": "Sony WH-1000XM5",
      "category": "Electronics",
      "image_url": "/images/products/sony-wh1000xm5.jpg",
      "quantity": 1,
      "unit_price": 299.99,
      "subtotal": 299.99
    }
  ],
  "status_history": [
    {
      "status": "placed",
      "notes": "Order received",
      "location": null,
      "timestamp": "2026-03-20T09:15:00+00:00"
    },
    {
      "status": "delivered",
      "notes": "Delivered to front door",
      "location": "New York, NY",
      "timestamp": "2026-03-23T14:30:00+00:00"
    }
  ],
  "return": null
}
```

当存在退货时，`return` 字段的内容为：

```json
{
  "id": "return-uuid-1",
  "reason": "Defective product",
  "status": "refunded",
  "refund_method": "original_payment",
  "refund_amount": 299.99,
  "created_at": "2026-03-25T10:00:00+00:00",
  "resolved_at": "2026-03-28T16:00:00+00:00"
}
```

**错误**
- `404` 订单不存在或不属于当前用户

---

## 个人资料

### GET /api/profile

获取已认证用户的个人资料，包括会员等级权益与活动计数。

| 字段 | 取值 |
|-------|-------|
| 认证  | 需要 JWT |

**响应** `200`

```json
{
  "id": "user-uuid-1",
  "email": "alice.johnson@gmail.com",
  "name": "Alice Johnson",
  "role": "customer",
  "loyalty_tier": "gold",
  "total_spend": 4200.0,
  "member_since": "2025-12-01T00:00:00+00:00",
  "order_count": 15,
  "review_count": 8,
  "tier_benefits": {
    "discount_pct": 10.0,
    "free_shipping_threshold": 25.0,
    "priority_support": true
  }
}
```

**错误**
- `404` 用户不存在

---

## 智能体市场

### GET /api/marketplace/agents

列出市场中所有上架的活跃智能体。

| 字段 | 取值 |
|-------|-------|
| 认证  | 需要 JWT |

**响应** `200`

```json
[
  {
    "id": "agent-uuid-1",
    "name": "product-discovery",
    "display_name": "Product Discovery",
    "description": "Searches products by keyword, category, price range, and semantic similarity.",
    "category": "Shopping",
    "icon": "search",
    "status": "active",
    "version": "1.0",
    "capabilities": ["search", "recommend", "compare"],
    "requires_approval": true,
    "allowed_roles": ["power_user", "admin"]
  }
]
```

---

### POST /api/marketplace/request

为指定智能体提交访问申请。

| 字段 | 取值 |
|-------|-------|
| 认证  | 需要 JWT |

**请求体**

```json
{
  "agent_name": "product-discovery",
  "role_requested": "power_user",
  "use_case": "I need advanced product search capabilities for comparison shopping."
}
```

**响应** `200`（待审批）

```json
{
  "id": "req-uuid-1",
  "agent_name": "product-discovery",
  "status": "pending",
  "message": "Your request has been submitted and is pending admin approval."
}
```

**响应** `200`（自动批准，当 `requires_approval = false` 时）

```json
{
  "id": "req-uuid-1",
  "agent_name": "product-discovery",
  "status": "approved",
  "message": "Access granted automatically — no approval required."
}
```

**错误**
- `404` 智能体不存在
- `409` 该智能体已存在待处理的申请
- `409` 用户已拥有该智能体的访问权限

---

### GET /api/marketplace/my-agents

列出已认证用户已获授权访问的智能体。

| 字段 | 取值 |
|-------|-------|
| 认证  | 需要 JWT |

**响应** `200`

```json
[
  {
    "agent_name": "product-discovery",
    "display_name": "Product Discovery",
    "description": "Searches products by keyword, category, price range, and semantic similarity.",
    "category": "Shopping",
    "icon": "search",
    "role": "power_user",
    "granted_at": "2026-03-01T12:00:00+00:00"
  }
]
```

---

## 管理员

所有管理员端点都要求 JWT 中携带 `role: "admin"`。非管理员用户会收到 `403 Admin access required`。

### GET /api/admin/requests

列出所有用户中待处理的访问申请。

| 字段 | 取值 |
|-------|-------|
| 认证  | 仅管理员 |

**响应** `200`

```json
[
  {
    "id": "req-uuid-1",
    "agent_name": "product-discovery",
    "role_requested": "power_user",
    "use_case": "I need advanced product search capabilities.",
    "status": "pending",
    "created_at": "2026-04-01T08:00:00+00:00",
    "user_email": "bob.smith@gmail.com",
    "user_name": "Bob Smith",
    "user_role": "customer"
  }
]
```

---

### POST /api/admin/requests/{request_id}/approve

批准一条待处理的访问申请，并在同一事务中创建对应的 `agent_permissions` 记录。

| 字段 | 取值 |
|-------|-------|
| 认证  | 仅管理员 |

**请求体**

```json
{
  "admin_notes": "Approved for trial period."
}
```

`admin_notes` 为可选字段（默认为空字符串）。

**响应** `200`

```json
{
  "status": "approved",
  "request_id": "req-uuid-1"
}
```

**错误**
- `404` 申请不存在
- `409` 申请已被批准/拒绝

---

### POST /api/admin/requests/{request_id}/deny

拒绝一条待处理的访问申请。

| 字段 | 取值 |
|-------|-------|
| 认证  | 仅管理员 |

**请求体**

```json
{
  "admin_notes": "Insufficient justification."
}
```

**响应** `200`

```json
{
  "status": "denied",
  "request_id": "req-uuid-1"
}
```

**错误**
- `404` 申请不存在
- `409` 申请已被批准/拒绝

---

### GET /api/admin/usage

获取最近 30 天的汇总使用统计，包含按智能体拆分的明细以及 7 天的每日趋势。

| 字段 | 取值 |
|-------|-------|
| 认证  | 仅管理员 |

**响应** `200`

```json
{
  "period": "last_30_days",
  "overall": {
    "total_requests": 1250,
    "unique_users": 18,
    "total_tokens_in": 450000,
    "total_tokens_out": 680000,
    "avg_duration_ms": 2300,
    "total_tool_calls": 3400
  },
  "by_agent": [
    {
      "agent_name": "orchestrator",
      "request_count": 500,
      "unique_users": 18,
      "tokens_in": 180000,
      "tokens_out": 250000,
      "avg_duration_ms": 2100,
      "error_count": 5
    },
    {
      "agent_name": "product-discovery",
      "request_count": 320,
      "unique_users": 15,
      "tokens_in": 95000,
      "tokens_out": 150000,
      "avg_duration_ms": 1800,
      "error_count": 2
    }
  ],
  "daily_trend": [
    {
      "day": "2026-04-04",
      "request_count": 85,
      "unique_users": 12
    },
    {
      "day": "2026-04-03",
      "request_count": 92,
      "unique_users": 14
    }
  ]
}
```

---

### GET /api/admin/audit

从 `usage_logs` 获取详细的审计日志，并为每条记录关联对应的 `agent_execution_steps`。

| 字段 | 取值 |
|-------|-------|
| 认证  | 仅管理员 |

**查询参数**

| 参数 | 类型 | 默认值 | 说明 |
|-----------|------|---------|-------------|
| `limit`   | int  | `50`    | 每页数量（最大 200） |
| `offset`  | int  | `0`     | 分页偏移量 |

**响应** `200`

```json
{
  "entries": [
    {
      "id": "log-uuid-1",
      "agent_name": "orchestrator",
      "user_email": "alice.johnson@gmail.com",
      "user_name": "Alice Johnson",
      "input_summary": "What are the best headphones?",
      "tokens_in": 150,
      "tokens_out": 420,
      "tool_calls_count": 2,
      "duration_ms": 2450,
      "status": "success",
      "error_message": null,
      "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
      "created_at": "2026-04-04T10:15:00+00:00",
      "steps": [
        {
          "step_index": 0,
          "tool_name": "call_specialist_agent",
          "tool_input": { "agent_name": "product-discovery" },
          "tool_output": { "result": "Found 3 matching products..." },
          "status": "success",
          "duration_ms": 1200
        }
      ]
    }
  ],
  "total": 1250,
  "limit": 50,
  "offset": 0
}
```

`trace_id` 字段与 Jaeger 中的 OpenTelemetry 追踪（trace）相关联，因此可以从审计日志下钻到分布式追踪。

---

## 错误响应格式

认证与常规异常通常包含 FastAPI 的 detail 字段；参数验证的 detail 可能是数组。售后业务响应还包含 outcome、error_code、operation_id 等结构化字段，不能仅凭 HTTP 状态码判断业务完成。常见形式：

```json
{
  "detail": "Description of the error"
}
```

| 状态码      | 含义 |
|-------------|---------|
| `401`       | JWT 缺失、已过期或无效 |
| `403`       | 权限不足（例如非管理员访问管理员路由） |
| `404`       | 资源不存在或不属于当前认证用户 |
| `409`       | 待审批、版本/参数冲突或状态已变化；读取当前状态后再决定下一步 |

---

## 相关文档

- [`docs/architecture.md`](architecture.md) — 请求如何从浏览器经编排器流向专业智能体
- [`docs/database-schema.md`](database-schema.md) — 这些端点读写的数据库表
- [`docs/deployment.md`](deployment.md) — 如何启动整个技术栈，使这些端点可被访问
- [`docs/frontend.md`](frontend.md) — Next.js 客户端如何调用这些端点
- [项目 README](../README.md)

## 任务与可修订上下文

任务创建、结构化验收、显式执行/恢复、目标修订及人工确认见[任务工作台接口](task-workspace.md)。

`GET /api/conversations/{id}/context` 读取本人会话当前目标、约束、来源和版本；`PATCH`携带`revision`及实际变更的`goal`/`constraints`。约束列表是明确提交的新列表，移除旧项表示撤销。版本冲突返回409，客户端需重新读取并审阅，不自动重发。目标编辑采用新epoch，专业智能体不得把模型生成的A2A消息当成人类新要求写回。

`GET /api/runtime/capabilities` 需要认证，仅返回非敏感运行能力，例如是否为不录制的离线演示、固定任务示例是否可用；不返回密钥、数据库连接地址或登录状态。


## 采购任务运行时（新增）

所有控制面接口复用用户认证和任务所有者隔离。旧查询任务的 `plan/next/recover/confirm` 不推进采购任务。

| 方法与路径 | 请求与行为 |
|---|---|
| `POST /api/tasks/parse` | `{goal}`；创建持久草案并提出结构化需求。解析也属于同一任务的模型调用预算 |
| `POST /api/tasks` | 可指定 `task_type=procurement` 和结构化 `requirements`，仍创建未确认草案 |
| `POST /api/tasks/{id}/confirm-spec` | `{revision, requirements}`；确认需求版本。未处理的不支持条件不能被静默确认 |
| `POST /api/tasks/{id}/start` | `{revision}`；202接收，后台自动执行；也用于已暂停任务继续 |
| `POST /api/tasks/{id}/pause` | `{revision}`；停止派发新节点，活动节点归档后暂停。终态可进入修订流程 |
| `PATCH /api/tasks/{id}` | `{revision, goal, constraints:[], requirements}`；同步修订目标与草案，保留历史；之后重新确认 |
| `GET /api/tasks/{id}` | 返回需求/计划版本、状态序号、步骤、证据、方案及累计计数 |
| `GET /api/tasks/{id}/events` | SSE只读事件；支持 `after_seq` 或 `Last-Event-ID` 续接，不重放控制操作 |

需求字段为 `query`、`quantity`、`alternative_count`、`total_budget`、`currency=CNY`、`in_stock=true`、`min_rating`、`preferences`、`budget_scope=goods`、`quote_policy=single_coupon_v1`。金额以十进制字符串传输。每个方案独立核实，不把备选方案合并成购物车。

`revision`用于用户控制面的CAS；后台事件通过`state_seq`递增。客户端按二者单调接收快照，409后应重新读取并让用户核对，不自动重试变更操作。

专业服务新增 `POST /procurement:execute`，只接受现有服务间认证，用户JWT不能直接调用。服务根据数据库冻结计划核对能力、对象和执行代次，真实工具结果独立于模型正文返回。这是项目内部协议，不声称新增标准A2A协议能力。
