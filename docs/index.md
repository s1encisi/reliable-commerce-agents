# 可靠电商多智能体平台

一个基于**微软智能体框架（Microsoft Agent Framework，MAF）**构建的多智能体电商平台，纯 **Python** 技术栈，概念说明与运行它的代码并排呈现。

六个专业智能体通过 A2A 协议（Agent-to-Agent，智能体间通信协议）协同工作，覆盖商品发现、订单管理、定价与促销、评论情感分析、库存与履约、售后支持。五种编排模式可在运行时从同一个对话框中选择，因此你可以看到同一个问题被路由到五种不同的实现路径，并比较各自的成本。

本站由仓库自动生成。这里的每个页面都是你可以在仓库中读到的文件，每个代码指针都指向真实的源码。

## 运行它

唯一的前置条件是 Docker —— 无需安装 Python 或 Node，也
[无需付费 API Key]({{ site.baseurl }}/getting-started/quick-start.html#run-without-a-paid-api-key)。

```bash
git clone https://github.com/s1encisi/reliable-commerce-agents.git
cd reliable-commerce-agents
cp .env.example .env          # 填入你的 OPENAI_API_KEY（或 Azure OpenAI 凭据）
./scripts/dev.sh              # 构建、灌入种子数据并启动全部服务
```

然后打开 **<http://localhost:3000>**，用 `zhangwei@example.com` / `customer123` 登录。

**在 Windows 上**，`scripts/dev.sh` 是 bash 脚本，无法在 PowerShell 中运行 —— 请改用功能等价的
PowerShell 版本，它接受相同的参数：

```powershell
Copy-Item .env.example .env    # 然后在 .env 中设置 OPENAI_API_KEY
./scripts/dev.ps1
```

→ **[完整快速开始]({{ site.baseurl }}/getting-started/quick-start.html)** —— 无需 API Key 的运行方式、WSL2 注意事项，以及出问题时该怎么办。

---

## 从哪里开始

| 如果你是…… | 从这里开始 |
|---|---|
| 刚接触智能体 —— 此前从未构建过 | [概念]({{ site.baseurl }}/concepts/) —— 什么是智能体、为什么要多个智能体、这里的「图」指什么 |
| 准备动手实现 | [教程]({{ site.baseurl }}/tutorials/) —— 34 章，每章都可在无需 API Key 的情况下运行 |
| 想直接把应用跑起来 | [快速开始]({{ site.baseurl }}/getting-started/) |
| 想评估架构 | [架构]({{ site.baseurl }}/architecture/) |
| 想了解各模块能力覆盖情况 | [能力矩阵]({{ site.baseurl }}/reference/parity-matrix.html) |

## 本项目的不同之处

大多数智能体框架示例只孤立地展示一种编排模式。本仓库把**同一个非平凡业务域用五种方式实现** —— 工具路由、处理权交接网络、两张工作流图，以及群聊圆桌 —— 并放在同一个可运行应用里，因此从业者真正关心的问题（「我该用哪一种？」）有了带延迟和 token 数据的答案。

它还做了示例通常跳过的部分：服务端事实核验（grounding），在答案离开服务端之前用数据库校验模型的结论；真正会拦截的护栏；破坏性操作上的人工审批；退款操作的幂等性；以及一套跑生产路径（而非其副本）的评测运行框架。

## 四个层次

每一层都以不同深度讲解同样的概念，并指出下一步该看什么：

- **[概念]({{ site.baseurl }}/concepts/)** —— 用平实的语言讲清思路，配一张图，并指向它真正发挥作用的地方。
- **[教程]({{ site.baseurl }}/tutorials/)** —— 自己动手实现，规模很小，一次只讲一个机制。
- **[架构]({{ site.baseurl }}/architecture/)** —— 整个系统如何拼合在一起。
- **代码** —— [在 GitHub 上](https://github.com/s1encisi/reliable-commerce-agents)，按完整规模实现。

## 项目当前状态

**v1.1。** 暂停—恢复式审批循环、服务端事实核验、涉及资金操作的幂等性、限流，以及五种编排模式均已稳定运行在 Python 技术栈上。

### 近期完成

- 追问会保留上下文。此前任何来自浏览器的对话轮次，专业智能体都收不到*任何*对话历史 —— 而且是确定性地复现，与此同时所有测试却都是通过的。
- Python 运行现在会出现在 Jaeger 视图中；此前不可见，是因为跨度（span）命名与 Jaeger 的选取约定不一致。
- 语义检索可用。它在重放模式下完全失效，而其下还藏着一个生产缺陷：IVFFlat 索引建在空表上，返回了不相关的商品。
- 促销可以生效了。种子数据写入方与读取方对 `promotions.rules` 的键名理解不一致，因此此前没有任何促销活动正确生效过。
- 本站可被搜索引擎索引 —— 逐页元数据，以及全部 71 张图上可访问的标题。

### 后续计划

- **评测数据集覆盖** —— 7 个数据集中已接入 6 个；录制运行、基线与 CI 门禁仍待补齐。
- **教程代码覆盖** —— 第 12–19 章有代码但缺少测试，第 22–32 章尚无配套示例。
- **编辑器体验** —— 上下文相关的提示词建议，以及可折叠的模式选择器。
- **检索** —— `search_products` 目前仍是 `ILIKE`；全文检索与混合检索已在计划中。

完整清单（包括本页未声称覆盖的缺口）见 [`.claude/plans/remaining-work.md`](https://github.com/s1encisi/reliable-commerce-agents/blob/26f47c494dd6b371312593e82f066713f6f56e9c/.claude/plans/remaining-work.md)。
