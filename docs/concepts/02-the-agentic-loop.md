# 智能体循环

> **初次接触？** 本页假定你已经了解这个概念，重点展示它在本项目中是如何实现的。

## 它是什么

智能体循环（agentic loop）是你每次调用 `agent.run(...)` 时运行的那个周期：模型**思考**（根据
此前的对话决定下一步做什么），可选地**调用工具**（要求运行框架用特定参数执行某个具体函数），
运行框架**观察**（真正执行那个函数，并把结果作为一条新消息交回模型），然后模型**再次思考**
——此时它已经看到了那个结果。这个过程反复进行，直到模型产出一段纯文本回答而不是工具调用
为止；此时循环结束，那段文本返回给调用 `agent.run(...)` 的一方。

关键之处在于，这是一个*循环*，而不是一次简单的请求/响应对。一个用户问题可以让模型经历好几轮
「调用工具、看结果、再调用另一个工具」，之后才会产出用户看到的那句话。

## 为什么重要

没有循环，「智能体」就只意味着「一个被允许调用某个函数的模型」。这对除了最简单的任务以外的
任何场景都不够用。想想「这款无线降噪耳机还有货吗？寄到上海要多久？」——要答好这个问题，至少
需要两次工具调用（先 `check_stock`，再查一次运费时效估算），而第二次调用的参数依赖于模型只有
在第一次调用返回*之后*才知道的信息。单次调用的模型做不到这一点；它只能凭空同时猜出两个答案。

循环还让「模型调用了一些函数」变成可以逐步检查和信任的东西：每一次迭代都是一个离散、可记录
的步骤——具体的工具、具体的参数、具体的结果——而不是一个不透明的黑箱答案。这正是
[事实核验](09-grounding-and-rag.md)和[智能体时间线 UI](#本项目怎么实现)得以成立的前提：每一步
都有具体的东西可查，而不是只有一个最终段落。

## 什么时候用——什么时候不用

用不用循环不是你能选的——只要你在用带工具的 `agent.run(...)`，你就用上了。真正需要做的设计
决定是：**你愿意让模型迭代多少次**，以及**对某个具体任务，是否应该允许模型自行决定工具调用
的顺序**。如果某个任务的工具调用序列无论如何都一样，那说明你可能并不需要循环带来的灵活性——
关于什么时候固定序列优于让模型临场发挥，见
[什么是智能体](01-what-is-an-agent.md#什么时候用什么时候不用)和
[智能体系统中的图](07-graphs-in-agent-systems.md)。

## 本项目怎么实现

本仓库没有自己实现这个循环——它是刻意不实现的。这个代码库的早期版本有一个手写的 OpenAI
chat-completions 循环；在确认微软智能体框架原生的 `agent.run()` 能覆盖它需要的全部能力
（包括流式输出和 Azure）之后，那段代码被删除了。循环本身位于 `agent_framework` 包内，不在本
仓库——你在这里能看到的是边界：循环在哪里被调用，以及它的结果落在哪里。

这个边界就是 [`agents/python/shared/agent_host.py`](../../agents/python/shared/agent_host.py)。非流式：

```python
# agents/python/shared/agent_host.py
response = await agent.run(messages, options=_run_options())
```

流式——同一个循环，但你可以看着每一步陆续到达，而不必等整段完成：

```python
# agents/python/shared/agent_host.py
stream = agent.run(messages, stream=True, options=_run_options())
async for update in stream:
```

沿着这条路径，循环做出的每一次工具调用都会被 `StepRecorderMiddleware`
（[`agents/python/shared/agent_observability.py`](../../agents/python/shared/agent_observability.py)）记录到该请求的步骤列表中。要证明
某个查询确实让循环跑了不止一轮，在浏览器里就能看到：打开聊天界面，问一个需要两个事实的问题
（比如上面那个库存加运费的例子），然后看
[`web/src/components/chat/agent-timeline.tsx`](../../web/src/components/chat/agent-timeline.tsx) 为每次工具调用渲染一行——`search_products`，
然后是 `check_stock`，再是最终回答——每一行都能展开，显示传入了什么参数、返回了什么
（[`agent-timeline.tsx`](../../web/src/components/chat/agent-timeline.tsx)）。那条时间线*就是*智能体循环的可视化。

```mermaid
%%{init: {'theme':'base', 'themeVariables': {
  'primaryColor': '#2563eb','primaryTextColor': '#ffffff','primaryBorderColor': '#1e40af',
  'lineColor': '#64748b','secondaryColor': '#f59e0b','tertiaryColor': '#10b981',
  'background': 'transparent'}}}%%
flowchart LR
  classDef core     fill:#2563eb,stroke:#1e40af,color:#ffffff
  classDef external fill:#f59e0b,stroke:#b45309,color:#000000
  classDef success  fill:#10b981,stroke:#047857,color:#ffffff

  start(["用户提问"]) --> think1["模型思考：<br/>我需要什么？"]
  think1 -->|想要工具| call1[["call_tool<br/>例如 check_stock"]]
  call1 --> observe1["运行框架执行它，<br/>结果追加到消息列表"]
  observe1 --> think2["模型再次思考，<br/>此时已有该结果"]
  think2 -->|想要另一个工具| call2[["call_tool<br/>例如 get_shipping_estimate"]]
  call2 --> observe2["结果追加"]
  observe2 --> think3["模型再次思考"]
  think3 -->|完成，输出纯文本| final(["最终回答返回给<br/>agent.run 调用方"])

  class start,final success
  class think1,think2,think3 core
  class call1,call2 external
```

下一页：[工具](03-tools.md) —— 到底什么才让一个 Python 函数成为模型有权调用的工具。
