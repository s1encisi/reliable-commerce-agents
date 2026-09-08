# 第 01 章：第一个智能体（Python 中文教学版）

[英文原文](README.md) · [全部章节](../README.zh-CN.md) · [Python 源码](python/main.py)

## 学习目标

理解最小 Agent 由模型客户端、指令和一次运行组成；识别外部模型调用与本地 Python 控制流程的边界。本章没有工具，也没有数据库。后续第 02 章在这个结构上添加工具。

## 输入、处理与输出

默认问题是 What is the capital of France? 指令要求简短回答地理问题。客户端把信息发送给模型，ask 返回 response.text，main 将问题和答案打印出来。

    用户问题 → Agent.run → 模型客户端 → 模型响应 → response.text

这张流程描述预期调用关系，不是本轮真实模型运行记录。

## 关键函数

| 函数 | 作用 |
|---|---|
| _default_client | 根据环境变量创建真实模型客户端或回放客户端 |
| build_agent | 将客户端、INSTRUCTIONS 和 name 组装为 Agent |
| ask | 等待 agent.run，然后返回文本 |
| main | 接收命令行问题或默认问题，创建并运行智能体 |

build_agent 接受已有 client，方便测试时注入受控客户端。依赖注入让测试能观察真正传给模型的消息，而无需每次访问外网。

async def 定义异步函数；await 等待异步结果。它并不意味着请求会自动无限并发，也不代表函数自行创建了另一个智能体。

## 环境与运行

在仓库根目录，完成教程依赖安装后：

    $env:LLM_PROVIDER = "replay"
    $env:RECORD = "false"
    uv run --project tutorials python tutorials/01-first-agent/python/main.py

本章回放记录对应固定问题与调用配置。改变问题后缺少 fixture 是回放条件不满足，不能解释成模型不会回答。

## 测试与证据

    uv run --project tutorials pytest tutorials/01-first-agent/python/tests -m "not integration" -v

[测试文件](python/tests/test_first_agent.py)包含受控客户端、回放及真实模型测试。阅读测试时关注它具体断言了什么：用户问题和指令是否到达客户端，ask 是否返回预期文本，以及记录用尽时怎样处理。

真实模型能否稳定遵守指令需要单独评估。本页没有声称这些测试已经在本机通过。

## 连接模型的边界

bootstrap 负责环境加载及兼容处理，属于运行准备。初学时先理解 build_agent 和 ask，再回头读 _default_client。真实服务使用的 API、模型名和鉴权方式以实际配置为准，不能只复制某个默认模型名就假定可用。

## 与完整项目的对应

完整项目的 [shared/agent_factory.py](../../agents/python/shared/agent_factory.py)集中构造模型客户端；[product_discovery/agent.py](../../agents/python/product_discovery/agent.py)在 Agent 上再增加工具、上下文提供器和中间件。

## 验收

能够解释：输入在哪里进入系统，模型在哪里被调用，输出在哪里提取，以及回放通过能够证明什么。下一步阅读 [第 02 章](../02-add-tools/README.zh-CN.md)。
