# 本地容器作品展示

本方案把前端、六个 Python 服务、PostgreSQL 和 Redis 放入独立 Compose 项目 `reliable-commerce-showcase`。只发布 `127.0.0.1:3020` 前端端口，API 和数据库不发布宿主端口；不会占用开发演示的 3010/8180。

## 启动与停止

需要 Docker Engine/Compose 和 Python 3。首次构建会下载官方基础镜像与锁定的依赖；不需要在宿主安装 Node 或项目 Python 依赖。

```bash
bash scripts/showcase.sh
bash scripts/showcase.sh status
bash scripts/showcase.sh stop
```

打开 http://localhost:3020。客户账号 `customer@example.test`，管理员账号 `admin@example.test`，公开合成密码均为 `DemoPass123!`。这些账号只适用于本机合成数据展示。

脚本首次生成 `.env.showcase.local`，权限 `0600`，只含随机数据库和内部认证密钥；该文件受 Git 忽略。重复启动保留密钥和已有记录。停止不会删除卷。本脚本没有重置原始数据库或删除数据卷的操作。

## 展示路径

1. 客户从订单 `aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1` 提交退货；另一个浏览器会话的管理员在“审批”查看原因、退款方式和审批依据。
2. 管理员批准后，客户查询请求状态，查看已创建申请；申请创建不等于退款到账。
3. “任务工作台”选择“离线固定示例（不调用模型）”，创建、生成计划、依次执行两步，查看真实数据库证据。该示例的固定目标是“筛选两款商品并核对库存”。

默认 `LLM_PROVIDER=replay`、`RECORD=false`、`APP_ENV_FILE=/dev/null`，所有模型 Key 显式为空。固定任务计划显示 `deterministic_demo`，它调用真实数据库工具并经过正常验收，不代表实时模型自由规划。其他自由模型请求若没有匹配夹具会失败，不会转而付费录制。

## 配置与隔离边界

数据库使用独立 `showcase-data` 卷。六个服务共享 `showcase-budget` POSIX 卷与同一个 `/var/lib/reliable-commerce/budget.json`，为以后经授权启用提供方时保留跨进程预算锁边界；当前离线运行不创建付费调用。

可通过 `SHOWCASE_PROVIDER_FILE=/绝对路径/配置文件` 把文件只读挂到 `/run/config/provider.env`。默认挂载仓库示例文件。此文件**不会被加载**；启用真实模型、配置价格及预算需要单独审阅和授权，不能仅靠挂载文件绕过离线默认值。

这是单机展示部署。只有前端绑定本机；服务内部网络可出站，但未配置真实模型密钥。外部履约模拟继续使用单独的本机演示，不纳入本容器网络。

## 录制与重复演示

若固定订单已使用，按[追加新合成订单](portfolio-demo.md#录制前追加新合成订单)为下一次录制创建新订单，不重置已有数据。该入口只接受专用演示数据库和固定合成客户/商品，输出新订单路径供真实浏览器流程使用。

## 本地验收

```bash
source scripts/dev-env.sh
uv run --project agents/python --no-sync python -m unittest scripts.test_showcase
E2E_BASE_URL=http://localhost:3020 E2E_DEMO_TASKS=1 pnpm --dir web exec playwright test e2e/tasks-live-demo.spec.ts
```

健康检查验证页面和各服务存活；售后与任务仍需浏览器/接口验收。只凭镜像构建成功不能说明业务链路成功。


## WSL中的前台展示

本机WSL使用systemd管理Docker。systemd服务本身不会保持发行版实例存活，关闭最后一个前台会话后可能停止实例；见[微软说明](https://learn.microsoft.com/en-us/windows/wsl/systemd)。本轮曾观察到实例自动退出并重新启动，不能只以一次健康检查宣称演示持续可用。

在WSL终端中使用：

```bash
bash scripts/showcase.sh serve
```

它先构建并等待健康，再以标准Compose前台模式保持会话。保持终端打开即可体验本机3020；Ctrl-C停止容器但保留数据卷。普通Linux常驻Docker环境仍可使用`up`后台启动。这里没有改全局WSL配置，也没有创建登录自启动任务。
