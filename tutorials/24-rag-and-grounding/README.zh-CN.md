# 第 24 章：检索与事实核验（Python 中文教学版）

[英文原文](README.md) · [全部章节](../README.zh-CN.md) · [Python 源码](python/main.py)

## 为什么学习这一章

模型在生成回答前拿到了正确资料，仍可能在输出时写错价格、商品编号或其他事实。检索解决“把资料放进上下文”，核验解决“输出中的可检查声明是否与资料一致”。

## 本章真实实现的范围

本章使用内存商品列表和简单关键词匹配演示检索，并用 ProductClaim、GroundingReport 等结构表示需要核验的声明和结果。

它没有实现完整的文档切分、向量索引、混合检索、重排或企业知识库。应把它视为检索与核验流程的最小示例，而不是已完成的完整 RAG 系统。

## 数据流

    用户问题 → 商品检索 → 结果进入模型上下文 → 生成回答
                                                ↓
                                  提取商品与价格声明
                                                ↓
                                  与可信商品数据比较

verify_claims 独立于模型决定调用哪个工具。它遍历声明，检查商品是否存在，以及价格是否在设定容差内匹配。

## 核验结果的含义

| 情况 | 解释 |
|---|---|
| verified | 本次被提取、被检查的声明与数据一致 |
| not_found | 没找到对应商品 |
| price_mismatch | 声明价格与参考价格不一致 |
| 没有提取到声明 | 没有被检查的对象，不能自动解释为整段回答可信 |

检查器只能检查它覆盖的字段和被提取出的声明。错误的提取器、过时的数据、单位不一致，都可能影响结论。

## 运行与测试

在仓库根目录：

    $env:LLM_PROVIDER = "replay"
    $env:RECORD = "false"
    uv run --project tutorials python tutorials/24-rag-and-grounding/python/main.py
    uv run --project tutorials pytest tutorials/24-rag-and-grounding/python/tests -m "not integration" -v

本轮没有执行这些业务测试。回放验证固定轨迹，不用于估计真实模型的价格和延迟。

## 与完整系统的对应

- [product_discovery/tools.py](../../agents/python/product_discovery/tools.py)：商品检索与数据库访问。
- [shared/grounding/verifier.py](../../agents/python/shared/grounding/verifier.py)：对工具记录与数据库核验。
- [shared/grounding/middleware.py](../../agents/python/shared/grounding/middleware.py)：运行结束后的核验处理。

当前完整系统的流式内容可能先发给用户，核验随后处理最终响应。需要严格保证关键事实先验后展示时，应明确输出门控及等待时间的代价。

## 迁移到售后场景

可核验事实包括订单归属、签收时间、退货状态和退款方式。语义解释可以由模型生成，资格判定必须依据明确业务规则。不能让模型用“用户似乎很着急”覆盖期限或权限条件。

## 验收

能够区分检索失败、生成错误、声明提取遗漏和参考数据错误；能说明为何“调用过检索工具”不是事实正确性的充分证据。
