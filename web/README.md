# Web —— 可靠电商多智能体平台前端

Next.js 16（App Router）+ React 19 + Tailwind CSS 4 + shadcn/ui。这里承载公开店铺、
智能体对话，以及面向多智能体后端的登录后账户控制台。

> **注意：** 这是 Next.js **16.x** —— API 与旧文档存在差异。改动框架相关代码前，
> 请先阅读 `node_modules/next/dist/docs/` 下对应的指南。参见 [`AGENTS.md`](./AGENTS.md)。

## 常用命令

```bash
pnpm install          # 安装依赖（使用 pnpm，而非 npm/yarn）
pnpm dev              # 开发服务器，地址 http://localhost:3000
pnpm build            # 生产构建
pnpm lint             # eslint
pnpm test             # vitest（单元/组件测试，jsdom 环境）
pnpm exec playwright test                        # 端到端测试（需应用已启动）
pnpm exec playwright test e2e/ui-smoke.spec.ts   # 不依赖后端的 UI 冒烟测试（模拟鉴权/接口）
```

`ORCHESTRATOR_URL` 指向编排服务（默认 `http://localhost:8080`）。它是**服务端**变量，
而不是 `NEXT_PUBLIC_*`：浏览器只会调用本应用自身的源，再由
`src/app/api/[...path]/route.ts` 将 `/api/*` 转发出去。因此该变量是按请求读取的，
而非编译进产物包——这正是同一份镜像能够对接任意后端的原因。

`NEXT_PUBLIC_API_URL` 仍可作为直连编排服务的备用方案，但会一并引入 CORS 问题。

## 目录结构

- `src/app/` —— App Router。
  - `/` 项目落地页；`shop/*` 公开店铺（首页、商品、购物车、
    助手）；`(app)/*` 需鉴权的账户控制台（首页看板、对话、
    订单、结算、个人资料、管理后台、卖家中心）；`login`、`signup`。
- `src/components/` —— `ui/`（shadcn 与基础组件：StatCard、Chart、Skeleton、
  ThemeToggle、命令面板）、`chat/`（RichMessage、商品/订单卡片、
  AgentTimeline）、`shop/`、`landing/`、`home/`、`sidebar`、`top-bar`。
- `src/lib/` —— `api.ts`（带类型的客户端 + SSE `chatStream`）、`auth-context`、
  `cart-context`、`nav.ts`、`motion.ts`、`scenarios.ts`、`format`、`images`。

路由、主题、SSE 流式/时间线契约，以及「公开页 vs 需鉴权页」的模型，详见
[`../docs/frontend.md`](../docs/frontend.md)。
