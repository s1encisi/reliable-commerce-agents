"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import {
  Store,
  ShoppingBag,
  Package,
  BadgePercent,
  Star,
  Boxes,
  LifeBuoy,
  ArrowRight,
  Workflow,
  Database,
  Cpu,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { pageEnter, listStagger, listItem, instant } from "@/lib/motion";

const REPO_URL = "https://github.com/s1encisi/reliable-commerce-agents";

interface AgentCard {
  name: string;
  blurb: string;
  icon: LucideIcon;
}

const AGENTS: AgentCard[] = [
  { name: "商品发现", blurb: "基于 pgvector 商品库的语义检索与推荐。", icon: ShoppingBag },
  { name: "订单管理", blurb: "下单、跟踪、取消与退货全流程处理。", icon: Package },
  { name: "定价与促销", blurb: "实时优惠、优惠券与价格明细。", icon: BadgePercent },
  { name: "评论与情感分析", blurb: "评论摘要与情感倾向信号。", icon: Star },
  { name: "库存与履约", blurb: "库存查询与发货、履约状态。", icon: Boxes },
  { name: "客户支持", blurb: "帮助、升级处理与账户问题。", icon: LifeBuoy },
];

const STACK = [
  "Microsoft Agent Framework",
  "A2A 协议",
  "Next.js 16",
  "PostgreSQL + pgvector",
  "OpenTelemetry → Jaeger",
];

export function Landing() {
  const reduce = useReducedMotion();

  return (
    <div className="min-h-screen bg-gradient-to-br from-background via-background to-muted">
      {/* 顶部导航 */}
      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 py-5 sm:px-6">
        <div className="flex items-center gap-2">
          <div className="flex size-8 items-center justify-center rounded-lg bg-primary">
            <Store className="size-4 text-primary-foreground" />
          </div>
          <span className="text-sm font-semibold tracking-tight">
            可靠电商多智能体平台
          </span>
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Button render={<Link href={REPO_URL} />} variant="ghost" size="sm">
            <span className="hidden sm:inline">GitHub</span>
            <ArrowRight className="size-3.5" />
          </Button>
          <Button render={<Link href="/login" />} size="sm">
            登录
          </Button>
        </div>
      </header>

      {/* 主视觉 */}
      <motion.section
        variants={reduce ? instant : pageEnter}
        initial="hidden"
        animate="visible"
        className="mx-auto max-w-3xl px-4 pb-10 pt-14 text-center sm:px-6 sm:pt-20"
      >
        <span className="inline-flex items-center gap-1.5 rounded-full border bg-card px-3 py-1 text-xs font-medium text-muted-foreground">
          <Cpu className="size-3.5 text-primary" />
          6 个专业智能体 · A2A 编排
        </span>
        <h1 className="mt-5 text-balance text-4xl font-bold tracking-tight sm:text-5xl">
          面向<span className="text-primary">电商业务</span>的多智能体平台
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-pretty text-base text-muted-foreground sm:text-lg">
          商品发现、订单、定价、评论、库存与客服 —— 专业智能体通过 A2A 协议协作，
          基于 Microsoft Agent Framework 构建。
        </p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Button render={<Link href="/shop" />} size="lg">
            立即体验 <ArrowRight className="size-4" />
          </Button>
          <Button
            render={<Link href={REPO_URL} />}
            variant="outline"
            size="lg"
          >
            查看源码
          </Button>
        </div>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-2">
          {STACK.map((s) => (
            <span
              key={s}
              className="rounded-full border bg-card/60 px-2.5 py-1 text-xs text-muted-foreground"
            >
              {s}
            </span>
          ))}
        </div>
      </motion.section>

      {/* 架构链路 */}
      <section className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
        <div className="grid items-stretch gap-3 sm:grid-cols-4">
          {[
            { label: "Next.js 前端", sub: "对话 + 商城", icon: Store },
            { label: "编排器", sub: "经 A2A 路由", icon: Workflow },
            { label: "6 个专业智能体", sub: "领域工具", icon: Cpu },
            { label: "PostgreSQL + Redis", sub: "pgvector + 缓存", icon: Database },
          ].map((n, i) => (
            <div key={n.label} className="relative">
              <div className="flex h-full flex-col items-center rounded-xl bg-card p-4 text-center ring-1 ring-foreground/10">
                <n.icon className="size-5 text-primary" />
                <p className="mt-2 text-sm font-semibold">{n.label}</p>
                <p className="text-xs text-muted-foreground">{n.sub}</p>
              </div>
              {i < 3 && (
                <ArrowRight className="absolute -right-2.5 top-1/2 hidden size-4 -translate-y-1/2 text-muted-foreground/50 sm:block" />
              )}
            </div>
          ))}
        </div>
      </section>

      {/* 智能体一览 */}
      <section className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
        <h2 className="text-center text-xl font-bold tracking-tight">
          认识这些智能体
        </h2>
        <p className="mt-1 text-center text-sm text-muted-foreground">
          每个都是独立微服务，拥有自己的工具与提示词。
        </p>
        <motion.div
          variants={reduce ? undefined : listStagger}
          initial={reduce ? undefined : "hidden"}
          animate={reduce ? undefined : "visible"}
          className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
        >
          {AGENTS.map((a) => (
            <motion.div
              key={a.name}
              variants={reduce ? undefined : listItem}
              className="rounded-xl bg-card p-4 ring-1 ring-foreground/10"
            >
              <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10">
                <a.icon className="size-4 text-primary" />
              </div>
              <p className="mt-3 text-sm font-semibold">{a.name}</p>
              <p className="mt-1 text-sm text-muted-foreground">{a.blurb}</p>
            </motion.div>
          ))}
        </motion.div>
      </section>

      {/* 行动号召 */}
      <section className="mx-auto max-w-3xl px-4 py-12 text-center sm:px-6">
        <div className="rounded-2xl bg-card p-8 ring-1 ring-foreground/10">
          <h2 className="text-xl font-bold tracking-tight">
            看看智能体如何协作
          </h2>
          <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
            使用预置的演示账号登录，让智能助手帮你找商品、查订单或使用优惠券。
          </p>
          <Button render={<Link href="/shop" />} size="lg" className="mt-5">
            启动演示 <ArrowRight className="size-4" />
          </Button>
        </div>
      </section>

      <footer className="mx-auto max-w-6xl px-4 py-8 text-center text-xs text-muted-foreground sm:px-6">
        可靠电商多智能体平台 ·{" "}
        <Link href={REPO_URL} className="text-primary hover:underline">
          开源仓库
        </Link>
      </footer>
    </div>
  );
}
