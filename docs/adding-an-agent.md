# 新增一个专业智能体

这是一份把新的专业智能体加入平台的分步清单。每个专业智能体都是独立微服务，编排器通过 A2A 访问它。

开始之前，请先阅读[架构 §3](architecture.md#3-智能体架构)，了解每个专业智能体都遵循的四文件结构；
以及 [MAF 最佳实践](maf-best-practices.md)，了解 `@tool` 的写法。

---

## 1. 搭建四文件模块

在 `agents/python/` 下创建一个以你的智能体命名的目录（snake_case）：

```
agents/python/your_agent/
├── __init__.py
├── agent.py      # create_your_agent() -> Agent
├── tools.py      # @tool 装饰的异步函数
├── prompts.py    # 从 YAML 加载的 SYSTEM_PROMPT
└── main.py       # A2AAgentHost 入口
```

### main.py

```python
from agent_framework_a2a import A2AAgentHost
from shared.telemetry import setup_telemetry
from shared.db import create_pool
from .agent import create_your_agent

app = A2AAgentHost(
    agent_factory=create_your_agent,
    host="0.0.0.0",
    port=8086,   # 选择下一个可用端口
).app

@app.on_event("startup")
async def startup():
    setup_telemetry(service_name="ecommerce.your-agent")
    await create_pool()
```

### agent.py

```python
from agent_framework import Agent
from shared.agent_factory import create_chat_client
from shared.context_providers import ECommerceContextProvider
from .tools import YOUR_TOOLS
from .prompts import SYSTEM_PROMPT

def create_your_agent() -> Agent:
    return Agent(
        client=create_chat_client(),
        system_prompt=SYSTEM_PROMPT,
        tools=YOUR_TOOLS,
        context_providers=[ECommerceContextProvider()],
    )
```

### tools.py

```python
from typing import Annotated
from agent_framework import tool
from shared.db import get_pool
from shared.context import current_user_email, current_user_role

@tool
async def my_tool(
    param: Annotated[str, "参数说明"],
) -> dict:
    """展示给 LLM 的一行说明。"""
    email = current_user_email.get()
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT ... FROM ... WHERE user_email = $1 AND ...",
            email,
        )
    return {"result": row}

YOUR_TOOLS = [my_tool]
```

关键规则（见 [MAF 最佳实践](maf-best-practices.md)）：
- 所有工具都必须是 `async`。
- 用户身份从 ContextVars 读取（`current_user_email`、`current_user_role`）—— 绝不作为参数接收。
- `@tool` 装饰器使用 `Annotated` 类型标注。不要使用 Pydantic 输入模型。
- 只能使用参数化 SQL —— `$1, $2` 语法，查询中不得出现 f-string。

### prompts.py

```python
from shared.prompt_loader import load_prompt

SYSTEM_PROMPT = load_prompt("your-agent")
```

---

## 2. 创建提示词 YAML

新增 `agents/python/config/prompts/your-agent.yaml`：

```yaml
role: |
  You are the Your Agent for 可靠电商多智能体平台. You handle [domain].

instructions: |
  - Use my_tool to [do something].
  - Always scope queries to the authenticated user.
  - [Agent-specific rules]

tools:
  - name: my_tool
    when_to_use: "When the user asks about [topic]"
```

`shared/prompt_loader.py` 中的 `load_prompt()` 会把它与共享的事实核验规则、模式上下文，
以及 `config/prompts/_shared/` 中的工具示例组装在一起。

---

## 3. 分配端口

从端口表中挑选下一个未使用的端口（目前 8080–8085 已被占用）。把它加到：

- `agents/python/your_agent/main.py` —— `port=8086`
- `docker-compose.yml` —— 新增服务条目（见第 4 步）
- `docs/deployment.md` 的端口表
- `README.md` 的 Port Map 小节

---

## 4. 添加 Docker Compose 服务

在 `docker-compose.yml` 的 `agents` 档位下添加：

```yaml
your-agent:
  build:
    context: ./agents/python
    args:
      AGENT_NAME: your_agent
      AGENT_PORT: "8086"
  ports:
    - "8086:8086"
  environment:
    - DATABASE_URL=${DATABASE_URL}
    - OPENAI_API_KEY=${OPENAI_API_KEY}
    - LLM_PROVIDER=${LLM_PROVIDER}
    - AGENT_SHARED_SECRET=${AGENT_SHARED_SECRET}
    - OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT}
    - OTEL_SERVICE_NAME=ecommerce.your-agent
  depends_on:
    db:
      condition: service_healthy
    jaeger:
      condition: service_started
  profiles: ["agents"]
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8086/health"]
    interval: 15s
    timeout: 5s
    retries: 3
    start_period: 30s
```

---

## 5. 向编排器注册

编排器通过 `AGENT_REGISTRY` 环境变量发现专业智能体 —— 这是一个「智能体名称 → 内部 URL」的 JSON 映射。

**在 `docker-compose.yml` 中**，把你的智能体加入编排器的 `AGENT_REGISTRY`：

```yaml
orchestrator:
  environment:
    AGENT_REGISTRY: >-
      {
        "product-discovery": "http://product-discovery:8081",
        "order-management": "http://order-management:8082",
        "pricing-promotions": "http://pricing-promotions:8083",
        "review-sentiment": "http://review-sentiment:8084",
        "inventory-fulfillment": "http://inventory-fulfillment:8085",
        "your-agent": "http://your-agent:8086"
      }
```

**在编排器的提示词 YAML 中**（`config/prompts/orchestrator.yaml`），添加一条路由规则，让 LLM 知道何时调用你的智能体：

```yaml
agents:
  - name: your-agent
    when_to_route: "When the user asks about [domain topic]"
    description: "Handles [what it handles]"
```

---

## 6. 编写测试

新增 `agents/python/tests/test_your_agent.py`。`tests/` 中已有的专业智能体测试就是要遵循的范式
—— 它们使用 `pytest-asyncio`、模拟数据库连接池，并在不调用 LLM 的前提下断言工具输出。

```python
import pytest
from unittest.mock import AsyncMock, patch
from your_agent.tools import my_tool
from shared.context import current_user_email

@pytest.mark.asyncio
async def test_my_tool_returns_result():
    current_user_email.set("zhangwei@example.com")
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {"id": "abc", "result": "value"}
    with patch("your_agent.tools.get_pool") as mock_pool:
        mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await my_tool(param="test")
    assert result["result"] == "value"
```

运行方式：

```bash
cd agents/python && uv run pytest tests/test_your_agent.py -v
```

---

## 7. 端到端验证

```bash
# 带上你的新智能体进行构建并启动
./scripts/dev.sh --clean             # PowerShell：./scripts/dev.ps1 -Clean

# 确认智能体健康
curl http://localhost:8086/health

# 发送一条应被路由到你的智能体的测试消息
curl -X POST http://localhost:8080/api/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "test message for your domain"}'

# 在 Jaeger 界面中确认路由
open http://localhost:16686
# 查找跨度中包含你的智能体名称的追踪
```

---

## 易踩的坑

- **ContextVars 会在请求之间重置** —— 不要把用户身份存到模块级状态里；每次工具调用都从
  `current_user_email.get()` 重新读取。
- **每个智能体一个连接池** —— 每个智能体都有自己的连接池（在 lifespan 中初始化）。
  不要跨进程共享连接池。
- **提示词 YAML 按请求加载** —— `load_prompt()` 每次调用都会重新读取 YAML，因此提示词无需重启即可热更新。
  这也意味着 YAML 语法错误会表现为运行时错误，而不是启动失败。
- **健康检查端点是免费的** —— `A2AAgentHost` 会自动注册 `/health`，你无需自己添加。
- **`AGENT_REGISTRY` 中的智能体名用连字符** —— 编排器提示词里用 `your-agent`（连字符），
  而 Python 模块用 `your_agent`（下划线）。这是两种不同的东西，不要混用。

---

## 相关内容

- [`docs/architecture.md §3`](architecture.md#3-智能体架构) —— 四文件智能体结构图
- [`docs/maf-best-practices.md`](maf-best-practices.md) —— `@tool`、中间件、提示词 YAML、ContextVars 的用法
- [`docs/agent-flows.md`](agent-flows.md) —— 多智能体协作在实际中如何运作
- [`docs/api-reference.md`](api-reference.md) —— 你的智能体所对接的编排器 REST API
- [项目 README](../README.md)
