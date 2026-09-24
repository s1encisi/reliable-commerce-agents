# 快速开始

在本地把整个平台跑起来 —— 六个智能体、Postgres、Redis 与 Web 界面。
**唯一的硬性要求是 Docker。** 你无需安装 Python、Node，也无需付费 API Key。

按你的机器选择对应小节。macOS 与 Linux 可以使用辅助脚本；**Windows 请直接使用
Docker Compose 命令**，因为 `scripts/dev.sh` 是 bash 脚本。

## 1. 获取代码并完成配置

各平台一致：

```bash
git clone https://github.com/s1encisi/reliable-commerce-agents.git
cd reliable-commerce-agents
cp .env.minimal .env
```

在 Windows PowerShell 中，最后一行改为 `copy .env.minimal .env`。

`.env.minimal` 只有一个变量。`.env.example` 是完整配置面 —— 涵盖所有认证模式、MCP、
OAuth、遥测与护栏设置 —— 它是参考资料，而不是起步模板。
[配置说明](configuration.md)解释了这一个文件如何传递到容器、宿主机上运行的 Python、
前端，以及它们读取方式并不完全相同的原因。

然后打开 `.env` 并设置模型提供方。以下任意一种都可用 —— 若不想使用付费服务，见
[无需付费 API Key 运行](#run-without-a-paid-api-key)：

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

## 2. 运行

有两条路径，差别大致是 1 分钟与 12 分钟。

| | `--demo` | 从源码构建 |
|---|---|---|
| 镜像来源 | 从 GHCR 拉取 | 在你的机器上构建 |
| 首次运行 | 约 1 分钟 | 约 12 分钟 |
| 架构支持 | `linux/amd64` 与 `linux/arm64` | 取决于你当前的平台 |
| 适用场景 | 只想看到它跑起来 | 你改动了代码 |

### 快速路径 —— 拉取预构建镜像

```bash
./scripts/dev.sh --demo          # macOS 与 Linux
./scripts/dev.ps1 -Demo          # Windows PowerShell
```

该命令会拉取十个已发布镜像、灌入种子数据、启动全部服务，并等待编排器与前端真正响应后
才打印摘要。没有任何编译过程。

`--demo` 使用 `docker-compose.demo.yml`，其中固定为 `:latest` —— 最新带标签的发布版本，
且已通过完整测试套件。若要运行 `main` 分支的最新提交：

```bash
IMAGE_TAG=main ./scripts/dev.sh --demo
```

如果你不想用脚本，直接 `docker compose` 也可以：

```bash
docker compose -f docker-compose.demo.yml up
```

各镜像标签的含义见[发布流程](releasing.md)。

### 从源码构建

```bash
./scripts/dev.sh                 # macOS 与 Linux
./scripts/dev.ps1                # Windows PowerShell
```

脚本会构建镜像、等待 Postgres 变为健康状态、以一次性任务方式运行种子数据脚本，
然后启动智能体与前端。最后还会打印所有 URL 的摘要。

### Windows

`scripts/dev.sh` 是 bash 脚本，无法在 PowerShell 或 `cmd` 中运行。你有两条路径，
但它们并不等价 —— 按你是否愿意在机器上使用 WSL2 来选择。

#### 推荐：WSL2

**[WSL2](https://learn.microsoft.com/windows/wsl/install) 能带来最佳体验**，而且如果你已经在
用 Docker Desktop，多半用的就是它的 WSL2 后端，因此这不会引入任何新的活动部件。
一切都会与 macOS、Linux 的文档描述完全一致 —— 辅助脚本、`--clean`/`--seed-only` 参数，
全部可用：

```bash
wsl                                   # 进入你的 Linux 发行版
git clone https://github.com/s1encisi/reliable-commerce-agents.git
cd reliable-commerce-agents
cp .env.example .env
./scripts/dev.sh
```

有两点需要注意：

- **把代码克隆到 Linux 文件系统内**（`~/reliable-commerce-agents`），而不是 `/mnt/c/` 下。
  跨 Windows 文件系统边界做绑定挂载会慢得多，这也是「为什么我的容器在 WSL2 上这么慢」的
  常见答案。
- **在 Docker Desktop 中启用 WSL 集成** —— *Settings → Resources → WSL Integration* ——
  并选中你正在使用的发行版，否则在 WSL 内找不到 `docker`。

Git Bash（随 [Git for Windows](https://git-scm.com/download/win) 提供）也能运行该脚本，
但只有 WSL2 才提供真正的 Linux 文件系统，因此那里的容器启动要快得多。

#### 不用 WSL2？PowerShell 也完全可行

有一个 PowerShell 脚本可以完成 `dev.sh` 的全部工作 —— 相同的档位、相同的执行顺序、
相同的健康检查、相同的参数：

```powershell
git clone https://github.com/s1encisi/reliable-commerce-agents.git
cd reliable-commerce-agents
Copy-Item .env.example .env
notepad .env                  # 设置 OPENAI_API_KEY，然后保存并关闭

./scripts/dev.ps1
```

如果 PowerShell 拒绝执行（`running scripts is disabled on this system`），那是执行策略的问题，
不是脚本的问题。要么一次性允许本地脚本 ——
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` —— 要么仅在本次运行中绕过：
`powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1`。

**或者完全跳过脚本。** 这里没有任何步骤必须依赖脚本；`docker compose` 在各平台上是同一个命令，
下面三行就是脚本所做的事（省去了健康轮询与结束时的摘要）：

```powershell
docker compose up -d db redis jaeger
docker compose --profile seed run --rm seeder
docker compose --profile agents --profile frontend up -d --build
```

然后打开 <http://localhost:3000>。

这些命令之间无需等待：seeder 声明了
`depends_on: db: {condition: service_healthy}`，因此 Compose 会阻塞它直到 Postgres 通过
健康检查。首次运行需要构建镜像，所以在有任何响应之前要等上几分钟。

**值得了解的 PowerShell 差异：**

| 不要用 | 改用 |
|---|---|
| `cp .env.example .env` | `Copy-Item .env.example .env` |
| 行尾的 `\`（续行） | 反引号 `` ` ``，或把命令写在一行内 |
| `./scripts/dev.sh --clean` | `./scripts/dev.ps1 -Clean` —— 或 `docker compose down -v` 后重新运行 |
| `./scripts/dev.sh --seed-only` | `./scripts/dev.ps1 -SeedOnly` —— 或 `docker compose --profile seed run --rm seeder` |
| `./scripts/dev.sh --infra-only` | `./scripts/dev.ps1 -InfraOnly` —— 或 `docker compose up -d db redis jaeger` |
| `lsof -i :3000`（端口冲突） | `netstat -ano \| findstr :3000` |
| `docker compose logs -f orchestrator` | 完全相同 —— Compose 命令不变 |

`dev.ps1` 在 macOS 与 Linux 上需要 **PowerShell 7+**
（[安装文档](https://learn.microsoft.com/powershell/scripting/install/installing-powershell)）；
在 Windows 上内置的 Windows PowerShell 5.1 就足够了。在 macOS 与 Linux 上，
`dev.sh` 仍是更符合习惯的选择 —— 两者可以互换。

停止全部服务：`docker compose down`。停止并同时清空数据库：
`docker compose down -v`。

{: .note }
> 如果你用的是 Git Bash 而不是 PowerShell，且 `./scripts/dev.sh` 报
> `bad interpreter: /bin/bash^M`，那是 Git 的 `core.autocrlf` 在检出时把脚本改写成了 CRLF，
> 并非脚本损坏。仓库内置了 `.gitattributes` 把 `*.sh` 固定为 LF，因此新克隆的仓库没有问题；
> 较旧的克隆需要执行 `git rm --cached -r . && git reset --hard` 才能生效。

### 任意平台的一行命令

如果你不想分步执行，下面这条命令会启动全部服务（含种子数据脚本）：

```bash
docker compose --profile seed --profile agents --profile frontend up --build
```

## 3. 打开它

| 内容 | URL |
|------|-----|
| **Web 应用** | <http://localhost:3000> |
| 编排器 API | <http://localhost:8080> |
| Jaeger 界面（追踪） | <http://localhost:16686> |

用任意一个种子账号登录 —— `zhangwei@example.com` / `customer123` 是一位带有历史订单的客户，
比全新账号更适合演示场景。完整列表见
[README 的测试用户表](https://github.com/s1encisi/reliable-commerce-agents#test-users)。

你也可以**无需登录**在 `/shop` 浏览商品目录并使用购物助手 ——
商品发现功能是匿名提供的。

## 无需付费 API Key 运行

以上任何步骤都不需要 OpenAI 订阅。任何兼容 OpenAI 的端点都走同一条代码路径 ——
设置 `LLM_BASE_URL` 并保留 `LLM_PROVIDER=openai`：

```dotenv
# Ollama —— 完全本地，无需账号、密钥，也不受限流
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama          # 任意非空字符串 —— Ollama 不校验
LLM_MODEL=qwen2.5:14b          # 必须是支持工具调用的模型 —— 见下文
```

先启动模型，并且**调大上下文窗口** —— 这是本地运行表现不如托管服务的最常见原因：

```bash
ollama pull qwen2.5:14b
OLLAMA_CONTEXT_LENGTH=64000 ollama serve
```

在显存不足 24 GiB 的机器上，Ollama 默认使用 4K 上下文。智能体循环不断累积工具结果，
几轮之内就会超过 4K，此时 Ollama 会**静默丢弃最早的消息 —— 从系统提示词开始** ——
既不报错，响应里也没有任何提示。症状就是得到一个自信、格式规范但错误的答案。
Ollama 官方文档建议智能体类负载至少使用 64000 token。


{: .warning }
> **请确认你的本地模型确实能调用工具。** 这里的每个专业智能体都依赖工具调用，
> 而失败是安静的：函数调用不可靠的模型会停止调用工具，转而开始编造商品名和价格，
> 而不是报错。
>
> 2026 年 8 月 21 日在「查库存 → 计算缺口 → 补货至再订货点」这一双工具多轮循环上实测：
> **`qwen2.5:14b`**、**`gemma4:12b`** 与 **`qwen3.5:9b`** 均通过 —— 工具调用顺序正确、
> 算术正确、能干净终止。这只是一个场景，不是基准测试：
> 请把它当作「9B 及以上档位在这里可用」的证据，而不是一份排名。
>
> 如果答案看起来合理，但智能体时间线里没有任何工具调用，那就是模型撑不住的征兆。

{: .warning }
> **第二种静默失败：推理模型可能什么都不返回。**
> 推荐默认使用 `qwen2.5:14b`，因为它不输出思考轨迹。推理模型会在答案*之前*插入一长段内部
> 独白，而这段独白同样计入输出预算 —— 因此较小的 `max_tokens` 会被思考过程耗尽，
> 回复变成**空内容**，且 `finish_reason` 是 `"length"` 而不是报错。
>
> 在相同提示词、相同 1,024 token 上限下实测：
>
> | 模型 | 思考轨迹 | finish_reason | 延迟 | 答案 |
> |---|---|---|---|---|
> | `qwen2.5:14b` | 无 | `stop` | 约 10 秒 | 有 |
> | `gemma4:12b` | 约 1,000 字符 | `stop` | 约 39 秒 | 有 |
> | `qwen3.5:9b` | 约 3,957 字符 | **`length`** | 约 65 秒 | **空** |
>
> 注意这里并不是越小越快 —— `qwen3.5:9b` 是三者中最小的，却比最大的那个慢 6.5 倍，
> 因为时间都花在思考上了。如果模型返回空白内容，先检查 `finish_reason`，
> 再下结论说它不能做工具调用；把 `max_tokens` 调大（4096 是安全下限），
> 或者换一个非推理模型。

在 Ollama 上，端点必须**能从容器内部**访问：在 Docker Desktop（macOS/Windows）上应使用
`http://host.docker.internal:11434/v1`，而不是 `localhost`。

## 其他命令

```bash
./scripts/dev.sh --clean        # 清空数据卷并从头重建
./scripts/dev.sh --infra-only   # 只启动 db、redis、jaeger
./scripts/dev.sh --seed-only    # 针对已有数据库重新运行种子数据脚本
```

对应的 Compose 命令是 `docker compose down -v`、`docker compose up -d db redis jaeger`，
以及 `docker compose --profile seed run --rm seeder`。

## 出问题时

先从[故障排查](./troubleshooting.md)看起 —— 它覆盖了我们已知的每一种首次运行失败情况，
包括端口冲突、种子数据脚本与数据库的竞态，以及缺失的向量嵌入。

最常见的两种：

- **3000 或 8080 端口已被占用。** 被其他服务占用了。如果冲突来自 Compose 之外，
  `docker compose down` 无法解决 —— 用 `lsof -i :3000`（macOS/Linux）或
  `netstat -ano | findstr :3000`（Windows）排查。
- **对话有回答但从不调用工具。** 几乎总是模型问题，而不是代码问题 —— 见上方警告。

## 下一步看什么

- [概念](./concepts/) —— 什么是智能体、为什么要多个智能体、这里的「图」指什么
- [教程](../tutorials/) —— 34 章，每章都可在无需 API Key 的情况下运行
- [架构](./architecture.md) —— 整个系统如何拼合在一起
- [部署](./deployment.md) —— 配置参考、档位、环境变量
