# 开发环境与待登录服务

项目需要 Python 3.12、uv、Node.js 22、pnpm 10.15.0、.NET SDK 10 和 Docker Compose。
Python 后端及两个 MCP 包、教程、前端和 .NET 示例分别安装，避免不同环境互相覆盖。

## 安装与检查

在仓库根目录执行：

```bash
bash scripts/install-deps.sh
# Docker 能直接访问镜像仓库时，同时预下载基础设施镜像：
bash scripts/install-deps.sh --images
```

本机 Chromium 的缺失运行库已解压到 `.local/browser-libs/`。新终端先执行
`source scripts/dev-env.sh`，即可使用这些库和本地工具；此脚本只影响当前终端。
内存较小的机器运行前端测试时使用 `pnpm --dir web exec vitest run --maxWorkers=1`。

脚本不登录账号、不创建云端资源、不调用付费模型，也不会覆盖已有 `.env`。
首次在其他机器安装时，先复制 `.env.example` 为 `.env`，填写所需配置并生成自己的认证密钥。
Chromium 系统库若缺失，执行 `cd web && pnpm exec playwright install-deps chromium`，此步骤可能需要本机管理员密码。

Python 的 `httpx[socks]` 支持本地 SOCKS 代理。Docker daemon 不自动继承终端的代理；
终端能下载软件但 `docker pull` 超时，需要为 Docker 单独配置代理，或用已配置代理的镜像工具下载并导入。
本机已在 `.local/bin/crane` 准备经过发布校验和验证的镜像下载工具，安装脚本的 `--images`
会优先使用它，并跳过已有镜像；它只对这些公开镜像使用临时匿名配置，不改动现有 Docker 登录信息。
Python 和 .NET 的 Testcontainers 分别还需要 `testcontainers/ryuk:0.8.1`、`testcontainers/ryuk:0.14.0`。
Aspire 镜像不含 shell，因此不再使用容器内 shell 健康探针；启动后可用
`curl -fL http://localhost:18888/ -o /dev/null` 检查面板实际可访问。

## 回来后登录或填写

| 服务 | 用途 | 操作 |
| --- | --- | --- |
| OpenAI 或 Azure OpenAI | 主项目和教程的模型调用，选择一个即可 | 在根目录 `.env` 填写对应 Key；Azure 还要 endpoint 和 deployment。已有变量说明见 `.env.example` |
| Hugging Face（可选） | 模型、数据集访问与下载 | 运行 `hf auth login`，按提示填写自己的访问令牌；用 `hf auth whoami` 检查 |
| OpenRouter 或其他兼容服务（可选） | 替代模型服务 | 使用 `.env.example` 已有的 `LLM_PROVIDER=openai`、`LLM_BASE_URL`、`OPENAI_API_KEY` 和 `LLM_MODEL` 配置 |
| Langfuse（可选） | 额外的模型调用观测 | 保留 `.env` 中的 `LANGFUSE_ENABLED=false`；以后填写 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY` 和 `LANGFUSE_HOST` 再启用 |
| Docker Hub（可选） | 私有镜像或匿名下载限额 | 需要时运行 `docker login`；本项目公开基础设施镜像不要求账号 |

Hugging Face 官方客户端安装在 uv 的独立工具环境中，包含 `hf`、`huggingface-cli` 和 `tiny-agents`。
本机另外保留了 `.env.huggingface.local` 空白配置，供稍后填写；它不会自动覆盖主项目的模型配置。
项目目前没有直接使用 Transformers、PyTorch 或 datasets；没有为尚未选定的模型安装整套 GPU 推理环境或下载模型权重。
访问受限模型可能还需要在模型页面同意其条款。登录用法见 [Hugging Face CLI 官方文档](https://huggingface.co/docs/huggingface_hub/en/guides/cli)。

Hugging Face 登录不会自动把主项目切换成 Hugging Face 推理。
其兼容聊天入口为 `https://router.huggingface.co/v1`，见 [官方 Inference Providers 文档](https://huggingface.co/docs/inference-providers/en/index)。
若选择它，需自行填写 Token 和支持工具调用的模型 ID；当前项目让聊天和 embeddings 共用 `LLM_BASE_URL`，
数据库要求 1536 维向量，所以不能仅替换聊天地址就认为全部检索功能已经接通。此路径需要选定模型后验证。

## 本机配置与启动

根目录 `.env` 保存后端设置；`web/.env.local` 的 `ORCHESTRATOR_URL=http://localhost:8080` 指向本机后端。
本机 `.env` 中的 agent 路由和遥测地址已改为 localhost，便于直接运行已安装的 Python 环境；
Docker Compose 为容器单独提供内部服务地址。基础设施已启动并初始化演示数据；标准 Docker
全栈启动仍会首次构建 agent 和前端镜像，需要能访问基础镜像仓库及软件源。
真实配置、令牌、工具缓存和测试日志不会提交；本地安装记录位于 `.claude/memory/`，日志位于 `.local/`。
认证是项目自带的 JWT/OAuth 服务，PostgreSQL、Redis 和 Aspire 不需要外部账号。

```bash
# 标准 Docker 启动：先填写 .env 中的模型凭据。
./scripts/dev.sh

# 也可只启动基础设施，再分别启动本机服务。
./scripts/dev.sh --infra-only
cd agents/python
uv run uvicorn orchestrator.main:app --port 8080 --reload
# 其他终端按 CLAUDE.md 启动各 specialist；另一个终端从仓库根目录启动前端：
pnpm --dir web dev
```

仅启动 orchestrator 不等于六个 agent 全部启动。
完整 LLM、向量生成和真实聊天测试必须在配置账号后进行。
