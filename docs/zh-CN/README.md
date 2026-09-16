# 中文文档导航

[项目首页](../../README.md) · [英文文档索引](../README.en.md)

本目录是当前本地快照的中文导读与教学说明，整理日期为 2026-09-07。文档涉及的已有行为来自源码阅读，后续设计明确标为拟实现。这里没有将历史实验结果包装成本轮验证。

## 核心文档

| 文档 | 解决的问题 |
|---|---|
| [工作目录与分支](workspace.md) | Demo、独立 worktree、源项目各是什么，在哪里修改 |
| [快速开始](quick-start.md) | Python 回放与完整 Docker 平台的不同启动条件 |
| [环境安装与账号配置](environment-setup.md) | 安装各语言依赖、浏览器与基础设施，保留稍后登录的服务入口 |
| [架构导读](architecture.md) | 模型、工具、协调器、数据库、中间件怎样协作 |
| [售后代码导航](after-sales-code-map.md) | 从资格查询到审批执行分别有哪些入口 |
| [安全与可靠性](security-and-reliability.md) | 已有保护、默认模式和仍需验证的边界 |
| [测试与评估](evaluation.md) | 哪类测试证明什么，怎样避免漂亮但无效的分数 |
| [学习指南](learning-guide.md) | 不从空白手写代码的逐阶段学习方式 |
| [售后可靠性技术方案](after-sales-reliability-plan.md) | 业务约束、恢复策略、故障矩阵、指标与验收计划 |
| [M2 退货规则升级](after-sales-m2.md) | 已实现的统一规则、审批前置、事务与入口验证，以及 M3 尚待解决的恢复窗口 |

## Python 教程中文入口

- [全部章节导航](../../tutorials/README.zh-CN.md)
- [第 01 章：第一个智能体](../../tutorials/01-first-agent/README.zh-CN.md)
- [第 02 章：添加工具](../../tutorials/02-add-tools/README.zh-CN.md)
- [第 24 章：检索与事实核验](../../tutorials/24-rag-and-grounding/README.zh-CN.md)
- [第 26 章：评估](../../tutorials/26-evals/README.zh-CN.md)

## 阅读约定

“已有”表示在基线源码中找到实现，不等于本机运行通过。“拟实现”表示技术方案中的设计。“待复现”表示风险来自静态代码分析，尚无本轮端到端实验证据。

中文化范围是说明文档和学习材料。其余教程、历史记录、法律文本、机器可读配置、运行提示词与前端界面继续保留上游内容。
