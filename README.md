# Reliable Commerce Agents

基于 Microsoft Agent Framework、FastAPI、PostgreSQL 和 Next.js 的电商智能体平台，重点实现售后操作的审批、幂等提交、未知结果核实与有界恢复。

## 运行

需要 Docker、Python 3.12、uv、Node.js 和 pnpm。安装项目依赖后运行离线作品集演示：

```bash
uv sync --project agents/python --all-packages --extra dev
pnpm --dir web install --frozen-lockfile
bash scripts/demo.sh
```

具体演示选项可通过 `bash scripts/demo.sh --help` 查看。演示使用独立的本地数据，不涉及真实退款。实时模型对话需要用户自行配置模型提供方；示例凭据不适用于公开部署。

## 代码

- `agents/python/`：智能体、审批与售后服务、任务状态、评测代码和测试。
- `web/`：前端界面及测试。
- `docker/`：数据库初始化与基础设施。
- `scripts/`：启动、演示与检查入口。

本地离线回归可以从 `agents/python/tests/` 运行；依赖 PostgreSQL/Redis 的测试需要 Docker。

基于 [nitin27may/e-commerce-agents](https://github.com/nitin27may/e-commerce-agents) 开发。许可证及原始版权声明见 [LICENSE](LICENSE)。
