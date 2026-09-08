# 第 26 章：智能体评估（Python 中文教学版）

[英文原文](README.md) · [全部章节](../README.zh-CN.md) · [Python 源码](python/main.py)

## 学习目标

把一个主观的“好像回答对了”，转化为输入、预期事实、评分函数和可重复执行的案例。进一步学会判断评分器本身是否有效。

## 本章组成

| 组件 | 作用 |
|---|---|
| EvalCase | 保存问题与 expected_facts |
| search_catalog | 查询内存商品价格和库存 |
| run_eval_suite | 逐案例调用智能体并评分 |
| score_deterministic | 检查预期字符串是否出现在回答中 |
| judge_response_stub | 使用启发式生成 JudgeVerdict |
| print_scorecard | 输出案例分数 |

judge_response_stub 是教学替身，不是另一次真实 LLM 评判调用。不能把两列高度一致的分数当作两个独立评估器交叉验证。

## 一个评分器可能怎样误导

确定性评分器在预期字符串出现在回答中时计分。因此：

- 15 可能匹配 150 的一部分。
- 回答中出现正确价格，也可能同时出现相反结论。
- 回答包含正确数字但对应错误商品，仍可能得分。
- expected_facts 为空时返回满分，需要在数据集层面防止空案例。
- 用户被拒绝后若文本仍包含预期字段，不能据此判断任务完成。

本章测试刻意展示部分边界，用来帮助读者理解评分的适用范围。

## 运行与测试

在仓库根目录：

    $env:LLM_PROVIDER = "replay"
    $env:RECORD = "false"
    uv run --project tutorials python tutorials/26-evals/python/main.py
    uv run --project tutorials pytest tutorials/26-evals/python/tests -m "not integration" -v

本轮没有运行真实模型评估，也不引用示例输出作为新实验结果。

## 完整项目评估入口

[evals/harness.py](../../agents/python/evals/harness.py)中的 ProductionRunner 用实际运行路径执行任务，而不是另写一份工具循环模拟生产系统。

[evals/README.md](../../agents/python/evals/README.md)说明真实模型、数据库与回放要求。完整系统的 groundedness scorer 与本章的子串教学例子不同，不能混为一谈。

## 售后改进采用的评估设计

每个案例同时检查：

1. 是否选中正确的订单和工具。
2. 是否遵守身份、期限和审批条件。
3. 工具失败后是否采取允许的恢复方式。
4. 数据库最终状态是否符合预期。
5. 重试是否产生重复业务效果。
6. 回复是否准确描述“完成、拒绝、待澄清或待核实”。

先划分开发案例与保留验收案例，再修改提示词、规则和参数。使用相同数据与故障种子比较基线与改进版本，按故障类别报告结果。

## 验收

能够设计一个会真正失败的案例，指出评分器可能漏掉什么；能够区分正确拒绝和成功执行；能够解释为什么一次最好结果不足以代表稳定性能。
