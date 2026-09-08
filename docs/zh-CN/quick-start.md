# 中文快速开始：先运行小示例

[首页](../../README.md) · [上游完整启动说明](../quick-start.md)

本页优先照顾 Windows PowerShell 与 Python 学习者。以下命令是操作说明，本轮只完成文档检查，没有安装依赖、启动容器或调用真实模型。

## 1. 检查工作位置和工具

    # 先在终端中进入你克隆的 Demo 仓库根目录。
    git branch --show-current
    python --version
    uv --version

Python 教程要求 Python 3.12+，详见 [tutorials/pyproject.toml](../../tutorials/pyproject.toml)。使用 uv 管理项目环境，避免把依赖直接安装进系统或 Anaconda base。

## 2. 安装教程依赖

    uv sync --project tutorials --extra dev

这一步可能联网下载 Python 包。教程共用一个 uv 项目；第 20b 章另有特殊说明。无需为了第 01、02 章启动 PostgreSQL、Redis、前端或 .NET。

## 3. 离线回放第一个示例

依赖安装完成后，使用已有录制响应：

    $env:LLM_PROVIDER = "replay"
    $env:RECORD = "false"
    uv run --project tutorials python tutorials/01-first-agent/python/main.py
    uv run --project tutorials python tutorials/02-add-tools/python/main.py

固定 RECORD=false 是为了避免缺录制时转入真实模型录制。不要用“回放模式运行新问题失败”判断真实模型能力；它可能只是没有对应 fixture。

这些环境变量只影响当前 PowerShell 会话。之后需要真实模型时，应显式切换 provider，并先确认服务、输入内容和费用预算。

## 4. 运行不调用真实模型的教程测试

分别运行，保持故障定位简单：

    uv run --project tutorials pytest tutorials/01-first-agent/python/tests -m "not integration" -v
    uv run --project tutorials pytest tutorials/02-add-tools/python/tests -m "not integration" -v

测试中的直接函数调用、模型回放、真实模型集成测试是三类不同证据。标记 integration 的测试可能调用外部模型，不能因为机器上碰巧已有密钥就默认运行。

## 5. 真实模型配置

实际模型客户端构造代码见各章 main.py 的 _default_client。教程 bootstrap 会读取项目根目录的 .env，但不会覆盖显式环境变量。

仅在 .env 不存在时复制模板：

    if (-not (Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.minimal' -Destination '.env'
    }

在本地编辑配置，不把密钥写入说明文档、截图、测试数据或版本控制。OpenAI、Azure OpenAI 与其他兼容服务的能力和接口未必相同，应根据当前服务文档核对。这里不承诺任意兼容端点都支持教程所需的工具调用接口。

## 6. 需要完整平台时

Docker 可用、模型配置明确后：

    ./scripts/dev.ps1

这条路径从当前源码构建。若只体验上游已发布版本：

    ./scripts/dev.ps1 -Demo

-Demo 拉取发布镜像，与当前分支源码可能不同。为了验证修改，应使用源码构建并记录提交号。

| 内容 | 默认地址 |
|---|---|
| 网页 | http://localhost:3000 |
| 协调器 API | http://localhost:8080 |
| Aspire | http://localhost:18888 |

脚本会准备并填充数据库。-Clean 会删除容器和数据卷，不作为日常排错的默认命令。先保留日志、检查配置和数据，再决定是否需要重建。

## 7. 常见问题与判断

| 现象 | 先检查 |
|---|---|
| 找不到 agent_framework | 是否通过教程的 uv 环境运行 |
| 找不到回放记录 | 问题、提示词和工具签名是否与 fixture 一致 |
| 无法连接模型 | provider、端点、鉴权和接口能力 |
| 端口冲突 | 是否同时运行原仓库和 Demo 的容器 |
| 修改代码后界面没变化 | 是否正在使用 -Demo 的预构建镜像 |
| 测试显示 skipped | 具体跳过原因；跳过不等于通过 |
| git 提示分支已被占用 | 是否在两个工作区检出同一个分支 |

下一步阅读 [第 02 章中文说明](../../tutorials/02-add-tools/README.zh-CN.md)，先解释一次工具调用，再做小范围修改。
