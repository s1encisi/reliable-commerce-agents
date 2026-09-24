"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import {
  Package,
  ShoppingCart,
  Bot,
  Sparkles,
  ArrowRight,
  Activity,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api } from "@/lib/api";
import { formatPrice, formatDate } from "@/lib/format";
import { productImageUrl } from "@/lib/images";
import { QUICK_PROMPTS, DEMO_SCENARIOS, chatPromptHref } from "@/lib/scenarios";
import { pageEnter, listStagger, listItem, instant } from "@/lib/motion";
import { StatCard } from "@/components/ui/stat-card";
import { SectionHeader } from "@/components/ui/section-header";
import { Skeleton } from "@/components/ui/skeleton";
import { OrderStatusBadge } from "@/components/status-badge";
import { ScenarioCard } from "@/components/demo/scenario-card";

interface HomeOrder {
  id: string;
  status: string;
  total: number;
  created_at: string;
}

interface HomeProduct {
  id: string;
  name: string;
  price: number;
  image_url?: string | null;
  category?: string | null;
  brand?: string | null;
}

function timeGreeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "早上好";
  if (h < 18) return "下午好";
  return "晚上好";
}

export default function HomePage() {
  const { user } = useAuth();
  const { cart } = useCart();
  const reduce = useReducedMotion();

  const [orders, setOrders] = useState<HomeOrder[] | null>(null);
  const [products, setProducts] = useState<HomeProduct[] | null>(null);

  useEffect(() => {
    api
      .getOrders()
      .then((r) => setOrders(((r?.orders ?? []) as HomeOrder[]).slice(0, 4)))
      .catch(() => setOrders([]));
    api
      .getProducts({ sort: "rating" })
      .then((r) =>
        setProducts(((r?.products ?? []) as HomeProduct[]).slice(0, 4)),
      )
      .catch(() => setProducts([]));
  }, []);

  const firstName = user?.name?.split(" ")[0] ?? "朋友";

  return (
    <motion.div
      variants={reduce ? instant : pageEnter}
      initial="hidden"
      animate="visible"
      className="mx-auto max-w-7xl space-y-8 px-4 py-8 sm:px-6 lg:px-8"
    >
      {/* 问候语与快捷提问 */}
      <div>
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {formatDate(new Date().toISOString())}
        </p>
        <h1 className="mt-1 flex items-center gap-2 text-2xl font-bold tracking-tight">
          <Sparkles className="size-6 text-primary" />
          {timeGreeting()}，{firstName}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          您的多智能体购物助手。随时提问，或继续上次的会话。
        </p>

        <div className="mt-4 flex flex-wrap gap-2">
          {QUICK_PROMPTS.map((s) => (
            <Link
              key={s.label}
              href={chatPromptHref(s.prompt)}
              className="rounded-full border bg-card px-3 py-1.5 text-sm text-foreground/80 transition-colors hover:border-primary/40 hover:bg-accent hover:text-foreground"
            >
              {s.label}
            </Link>
          ))}
          <Link
            href="/chat"
            className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >
            进入对话 <ArrowRight className="size-3.5" />
          </Link>
        </div>
      </div>

      {/* 统计行 */}
      <motion.div
        variants={reduce ? undefined : listStagger}
        initial={reduce ? undefined : "hidden"}
        animate={reduce ? undefined : "visible"}
        className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"
      >
        {[
          {
            label: "我的订单",
            value: orders == null ? "—" : orders.length,
            icon: Package,
            hint: "近期",
          },
          {
            label: "购物车商品",
            value: cart?.item_count ?? 0,
            icon: ShoppingCart,
            hint: cart?.subtotal ? formatPrice(cart.subtotal) : "空",
          },
          {
            label: "专业智能体",
            value: 6,
            icon: Bot,
            hint: "协同工作",
          },
          {
            label: "平均响应",
            value: "~1.2s",
            icon: Activity,
            hint: "全部智能体",
          },
        ].map((s) => (
          <motion.div key={s.label} variants={reduce ? undefined : listItem}>
            <StatCard
              label={s.label}
              value={s.value}
              icon={s.icon}
              hint={s.hint}
            />
          </motion.div>
        ))}
      </motion.div>

      {/* 主网格 */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* 最近订单 */}
        <div className="rounded-xl bg-card ring-1 ring-foreground/10 lg:col-span-2">
          <div className="flex items-center justify-between px-4 py-3">
            <h2 className="text-sm font-semibold">最近订单</h2>
            <Link
              href="/orders"
              className="text-xs font-medium text-primary hover:underline"
            >
              查看全部
            </Link>
          </div>
          <div className="border-t">
            {orders == null ? (
              <div className="space-y-3 p-4">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : orders.length === 0 ? (
              <div className="px-4 py-10 text-center text-sm text-muted-foreground">
                暂无订单。{" "}
                <Link href="/products" className="text-primary hover:underline">
                  浏览商品
                </Link>
              </div>
            ) : (
              <ul className="divide-y">
                {orders.map((o) => (
                  <li key={o.id}>
                    <Link
                      href={`/orders/${o.id}`}
                      className="flex items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-accent/50"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">
                          订单 #{o.id.slice(0, 8)}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {formatDate(o.created_at)}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-3">
                        <OrderStatusBadge status={o.status} />
                        <span className="text-sm font-medium tabular-nums">
                          {formatPrice(o.total)}
                        </span>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* 购物车快照与智能体动态 */}
        <div className="space-y-6">
          <div className="rounded-xl bg-card p-4 ring-1 ring-foreground/10">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">我的购物车</h2>
              <ShoppingCart className="size-4 text-muted-foreground" />
            </div>
            <p className="mt-3 text-2xl font-semibold tabular-nums">
              {cart?.item_count ?? 0}{" "}
              <span className="text-sm font-normal text-muted-foreground">
                件商品
              </span>
            </p>
            <p className="text-sm text-muted-foreground">
              小计 {formatPrice(cart?.subtotal ?? 0)}
            </p>
            <Link
              href="/cart"
              className="mt-4 inline-flex w-full items-center justify-center gap-1 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
            >
              前往购物车 <ArrowRight className="size-3.5" />
            </Link>
          </div>

          <div className="rounded-xl border border-dashed bg-card/50 p-4">
            <div className="flex items-center gap-2">
              <Activity className="size-4 text-primary" />
              <h2 className="text-sm font-semibold">智能体动态</h2>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              展示各专业智能体处理您请求的实时时间线即将上线。
            </p>
          </div>
        </div>
      </div>

      {/* 演示场景 */}
      <div>
        <SectionHeader
          eyebrow="看智能体如何工作"
          title="演示场景"
          action={
            <Link
              href="/agents"
              className="text-xs font-medium text-primary hover:underline"
            >
              查看全部智能体
            </Link>
          }
        />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {DEMO_SCENARIOS.map((s) => (
            <ScenarioCard
              key={s.id}
              scenario={s}
              href={chatPromptHref(s.prompt)}
            />
          ))}
        </div>
      </div>

      {/* 推荐商品 */}
      <div>
        <SectionHeader
          eyebrow="为您推荐"
          title="推荐商品"
          action={
            <Link
              href="/products"
              className="text-xs font-medium text-primary hover:underline"
            >
              浏览全部
            </Link>
          }
        />
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {products == null
            ? [0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-56 w-full rounded-xl" />
              ))
            : products.map((p) => (
                <Link
                  key={p.id}
                  href={`/products/${p.id}`}
                  className="group/card overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10 transition-shadow hover:shadow-md"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={productImageUrl(p.id, 400, 300, p.image_url, p.category)}
                    alt={p.name}
                    className="h-36 w-full object-cover"
                  />
                  <div className="p-3">
                    <p className="line-clamp-1 text-sm font-medium">{p.name}</p>
                    {p.brand && (
                      <p className="text-xs text-muted-foreground">{p.brand}</p>
                    )}
                    <p className="mt-1 text-sm font-semibold text-primary">
                      {formatPrice(p.price)}
                    </p>
                  </div>
                </Link>
              ))}
        </div>
      </div>
    </motion.div>
  );
}
