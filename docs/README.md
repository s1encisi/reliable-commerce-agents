# 项目文档

面向第一次阅读、运行和技术追问，建议按下面四个入口理解当前 Python 项目。

| 入口 | 内容 |
|---|---|
| [项目首页](../README.md) | 解决的问题、实现范围、运行和证据入口 |
| [本地演示](portfolio-demo.md) | 客户申请、管理员审批、回执查询与异常案例 |
| [售后架构](after-sales-architecture.md) | 可信身份、统一规则、事务、幂等与中断恢复 |
| [验证记录](verification.md) | 当前与历史版本的结果、逐案例证据和适用边界 |

## 增强能力

- [采购决策工作台](procurement-demo.md)：中文需求确认、自动取证、预算修订与两个独立备选。
- [采购执行与恢复架构](procurement-architecture.md)：MAF分层并行、步骤回执、版本隔离和有界修复。
- [采购对照评测](procurement-evaluation.md)：共同任务、逻辑故障事件、顺序策略与消融，真实模型效果另计。

- [任务工作台](task-workspace.md)：受控验收、自然语言人工复核、版本和恢复。
- [中文检索评测](retrieval-evaluation.md)：候选召回、业务过滤、降级及保留的失败。
- [外部履约模拟](fulfillment-simulation.md)：本机供应商、outbox和对账。
- [容器体验部署](local-showcase.md)：独立本机环境、构建、停止与保留数据。
- [本地追踪与指标验收](local-telemetry-verification.md)：真实HTTP/SQL父子链、跨服务传播、低基数指标与内容过滤。
- [中文项目讲解](portfolio-interview.md)：架构讲解、简历模板、贡献边界与演示顺序。

## 协作与本地数据

[贡献与复验规范](../CONTRIBUTING.md) · [本地隐私与数据边界](local-privacy.md)

## 深入参考

[API](api-reference.md) · [配置](configuration.md) · [模型路由与预算](agent-upgrade.md) · [前端](frontend.md) · [教程](../tutorials/README.md)

专题与教程包含上游参考能力，不能据此推断当前业务入口已接入所有示例。当前代码、测试和注明版本的执行证据优先于旧计划；本地内部工作记录不作为公开项目能力证明。
