# 第 02 章：添加工具（Python 中文教学版）

[英文原文](README.md) · [全部章节](../README.zh-CN.md) · [Python 源码](python/main.py)

## 学习目标

掌握“模型提出工具调用，框架执行 Python 函数，再把结果交回模型”的过程。模型看到工具名称、用途和参数格式，而不是直接获得任意执行电脑程序的权限。

## 本章的数据

get_product_price 查询固定 Python 字典，输入是商品编号 sku，输出是字符串：

| 商品编号 | 固定价格 | 商品 |
|---|---|---|
| SKU-001 | 79.99 美元 | Wireless Mouse |
| SKU-002 | 129.99 美元 | Mechanical Keyboard |
| SKU-003 | 45.50 美元 | USB-C Hub |
| SKU-004 | 249.00 美元 | 27-inch Monitor |

这些是教学数据，没有访问真实价格 API。其他章节可能使用不同商品和价格，不应混用。

## 四处核心代码

| 位置 | 要理解的内容 |
|---|---|
| INSTRUCTIONS | 提示模型在商品价格问题上调用工具 |
| @tool 与 get_product_price | 声明工具、参数说明，并实现查询 |
| build_agent 的 tools 参数 | 真正把工具注册给智能体 |
| ask 中的 agent.run | 框架驱动模型与工具的交互 |

函数核心：

    return canned.get(sku.lower(), f"No pricing data for {sku}.")

sku.lower 统一大小写；字典 get 在没有对应商品时返回默认提示。Annotated 和 Field 为参数附加类型及用途说明；返回标注 str 表示预期输出类型。

@tool 包装后对象是框架工具。需要在单元测试中直接调用原函数时，当前实现使用 get_product_price.func(...)，以源码及测试为准。

## 预期执行链路

    用户询问 SKU-001 的价格
      → 模型选择 get_product_price，生成参数
      → 框架调用 Python 函数
      → 字典返回价格与商品名
      → 框架将结果交回模型
      → 模型组织最终回答

也存在模型直接回答而不调用工具的路径。提示词指导行为，实际是否调用要查看轨迹或可验证的调用记录。

## 运行

在仓库根目录：

    $env:LLM_PROVIDER = "replay"
    $env:RECORD = "false"
    uv run --project tutorials python tutorials/02-add-tools/python/main.py
    uv run --project tutorials pytest tutorials/02-add-tools/python/tests -m "not integration" -v

当前章节本身不需要数据库。切换真实模型前先确定接口配置和调用预算。

## 如何审阅测试

[现有测试](python/tests/test_add_tools.py)分别检查已知商品、未知商品、大小写、工具注册和模型回放。部分涉及调用行为的测试主要观察回答是否含预期价格或商品名。

例如断言“包含 79.99 或 wireless mouse”会允许“商品名称正确而价格错误”的回答通过，也没有直接证明真实工具执行。因此后续练习可以分别验证：

1. 是否真的调用工具。
2. 参数是否指向正确商品。
3. 工具返回是否正确。
4. 最终回答中的价格是否与工具结果一致。

这是拟开展的改进练习，本轮只说明边界，没有改变测试代码。

## 与售后项目的对应

[shared/tools/return_tools.py](../../agents/python/shared/tools/return_tools.py)沿用同样的工具定义方式，但需要数据库、身份、业务条件和副作用控制。

价格查询是只读操作，创建退货是写操作。发生超时以后，两者允许采用的重试策略不同，这是从本章走向可靠性改进时最重要的区别之一。

## 验收

解释价格来源、未知 SKU 行为、工具注册和真实执行的区别。能够指出“回答看起来正确”为什么不足以证明系统可靠。
