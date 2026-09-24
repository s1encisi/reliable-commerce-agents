# Mermaid 图表风格指南

所有章节的图表都遵循本指南，以保证整个系列阅读体验一致，且每张图在文档站点的明暗两套主题下都能正常渲染。

## 为什么要有这份指南

Mermaid 的默认主题是按浅色背景调校的。在深色页面上，它会渲染出泛白模糊的节点和难以辨认的箭头标签。本指南固定了一套在明暗两种模式下都有足够对比度的调色板（符合 WCAG AA，文字与其填充色对比度 ≥ 4.5:1），并提供一小组可复用的语义化 class。

## 调色板

| Class      | 用途                                    | 填充色      | 描边色      | 文字色   |
|------------|-----------------------------------------|-------------|-------------|----------|
| `core`     | 核心服务、智能体、MAF 原语              | `#2563eb`   | `#1e40af`   | `#ffffff`|
| `external` | 外部 API、LLM、MCP 服务器               | `#f59e0b`   | `#b45309`   | `#000000`|
| `success`  | 校验通过的输出、成功路径                | `#10b981`   | `#047857`   | `#ffffff`|
| `error`    | 错误路径、安全边界                      | `#ef4444`   | `#b91c1c`   | `#ffffff`|
| `infra`    | 数据库、缓存、基础设施、支撑组件        | `#64748b`   | `#334155`   | `#ffffff`|

不得使用其他颜色。不得使用渐变。节点标签中不得使用 emoji。

## 样板代码 —— 复制到每张图的顶部

```
%%{init: {'theme':'base', 'themeVariables': {
  'primaryColor': '#2563eb',
  'primaryTextColor': '#ffffff',
  'primaryBorderColor': '#1e40af',
  'lineColor': '#64748b',
  'secondaryColor': '#f59e0b',
  'tertiaryColor': '#10b981',
  'background': 'transparent'
}}}%%
flowchart LR
  classDef core     fill:#2563eb,stroke:#1e40af,color:#ffffff
  classDef external fill:#f59e0b,stroke:#b45309,color:#000000
  classDef success  fill:#10b981,stroke:#047857,color:#ffffff
  classDef error    fill:#ef4444,stroke:#b91c1c,color:#ffffff
  classDef infra    fill:#64748b,stroke:#334155,color:#ffffff
```

然后用 `class` 语句给节点指定 class：

```
class userAgent core
class openai external
class postgres infra
```

## 示例 —— 工具调用循环（第 02 章）

```
%%{init: {'theme':'base', 'themeVariables': {
  'primaryColor': '#2563eb','primaryTextColor': '#ffffff','primaryBorderColor': '#1e40af',
  'lineColor': '#64748b','secondaryColor': '#f59e0b','tertiaryColor': '#10b981',
  'background': 'transparent'}}}%%
flowchart LR
  classDef core     fill:#2563eb,stroke:#1e40af,color:#ffffff
  classDef external fill:#f59e0b,stroke:#b45309,color:#000000
  classDef success  fill:#10b981,stroke:#047857,color:#ffffff

  user([用户提问])
  agent[智能体]
  llm[(LLM)]
  tool[[get_weather 工具]]
  answer([最终回答])

  user --> agent
  agent -- "提示词 + 工具模式" --> llm
  llm -- "决定调用工具" --> agent
  agent -- "调用函数" --> tool
  tool -- "结果" --> agent
  agent -- "把结果放入上下文" --> llm
  llm -- "最终文本" --> agent
  agent --> answer

  class agent core
  class llm external
  class tool core
  class answer success
```

节点用形状来强化含义：`([圆角])` 表示面向用户的部分，`[矩形]` 表示服务/智能体，`[(圆柱)]` 表示数据存储/LLM，`[[六边形]]` 表示工具/函数。

## 支持的图表类型

| 图表类型          | 适用场景                                          |
|-------------------|---------------------------------------------------|
| `flowchart`       | 组件关系、数据流、管线                            |
| `sequenceDiagram` | 按时间排序的消息往来（A2A、HITL、流式输出）        |
| `stateDiagram-v2` | 生命周期（会话、检查点、Magentic 管理者）          |
| `classDiagram`    | 很少用 —— 仅在继承/组合关系本身就是重点时使用      |

避免使用 `gantt`、`pie`、`journey`、`quadrantChart` —— 它们不遵循本调色板。

## 规则

1. **每章至少一张图。** 放在「核心概念」小节，位于任何代码之前。
2. **原样复制 init 代码块。** 不要按章节调整颜色。
3. **给每个节点指定 class。** 未指定 class 的节点会退回 Mermaid 默认样式，在深色模式下外观不一致。
4. **节点标签要短。** 控制在 40 个字符以内。动作类含义用边标签表达。
5. **标签中不得使用 emoji**（遵循项目约定）。
6. **优先使用水平布局（`LR`）而非垂直布局（`TD`）**，除非该概念本身确实是层级结构。
7. **长流程用子图折成 2 行**，而不是画成一张巨大的 DAG。
8. **在图表下方加一句图注。** 用一句话点明这张图证明了什么：*「LLM 从不执行函数本身 —— 它请框架去执行，然后在下一个上下文窗口中看到结果。」*

## 验证

提交图表之前：

1. 在文档站点本地预览并切换明暗主题 —— 每个节点都必须保持可读。
2. 若可用，运行 Mermaid CLI：`npx -y @mermaid-js/mermaid-cli -i diagram.mmd -o /tmp/d.svg` —— 报错即视为构建失败。
3. 保持图表在 25 个节点以内。超过就拆成两张图。
