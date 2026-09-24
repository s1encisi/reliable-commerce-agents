"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { pageEnter, listStagger, listItem, instant } from "@/lib/motion";
import { ProductGridCard, type ShopProduct } from "@/components/shop/product-grid-card";
import { SectionHeader } from "@/components/ui/section-header";
import { Skeleton } from "@/components/ui/skeleton";
import { categoryImageUrl } from "@/lib/images";
import { DEMO_SCENARIOS, shopAssistantHref } from "@/lib/scenarios";
import { ScenarioCard } from "@/components/demo/scenario-card";

export default function ShopHome() {
  const reduce = useReducedMotion();
  const [products, setProducts] = useState<ShopProduct[] | null>(null);
  const [categories, setCategories] = useState<string[]>([]);

  useEffect(() => {
    api
      .getProducts({ sort: "rating" })
      .then((r) => {
        setProducts(((r?.products ?? []) as ShopProduct[]).slice(0, 8));
        setCategories((r?.categories ?? []).slice(0, 6));
      })
      .catch(() => setProducts([]));
  }, []);

  return (
    <motion.div
      variants={reduce ? instant : pageEnter}
      initial="hidden"
      animate="visible"
    >
      {/* 首屏 */}
      <section className="border-b bg-gradient-to-b from-muted/50 to-background">
        <div className="mx-auto max-w-7xl px-4 py-16 text-center sm:px-6 sm:py-20">
          <span className="inline-flex items-center gap-1.5 rounded-full border bg-card px-3 py-1 text-xs font-medium text-muted-foreground">
            <Sparkles className="size-3.5 text-primary" />
            与 AI 助手一起购物
          </span>
          <h1 className="mt-5 text-balance text-4xl font-bold tracking-tight sm:text-5xl">
            更快找到您想要的商品
          </h1>
          <p className="mx-auto mt-4 max-w-xl text-pretty text-muted-foreground sm:text-lg">
            浏览商品目录，或直接描述您的需求 ——
            我们的专业智能体可在数秒内完成搜索、对比与推荐。
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Link
              href="/shop/assistant"
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-primary-foreground shadow-sm transition-opacity hover:opacity-90"
            >
              <Sparkles className="size-4" /> 咨询 AI 助手
            </Link>
            <Link
              href="/shop/products"
              className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-4 py-2.5 text-sm font-medium transition-colors hover:bg-accent"
            >
              浏览商品 <ArrowRight className="size-4" />
            </Link>
          </div>
          <p className="mt-3 text-xs text-muted-foreground/70">
            试试：「300 元以内的无线耳机」·「对比索尼 WH-1000XM5 与 AirPods Max」
          </p>
        </div>
      </section>

      {/* 演示场景 —— 无需登录即可查看 */}
      <div className="border-b bg-muted/30">
        <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
          <SectionHeader
            eyebrow="看看实际效果"
            title="试试演示场景"
          />
          <p className="mb-6 max-w-xl text-sm text-muted-foreground">
            每个提示词会调用不同的专业智能体组合。点击任意卡片即可打开已预填该提示词的 AI 助手。
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {DEMO_SCENARIOS.map((s) => (
              <ScenarioCard
                key={s.id}
                scenario={s}
                href={shopAssistantHref(s.prompt)}
              />
            ))}
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-7xl space-y-12 px-4 py-12 sm:px-6">
        {/* 品类 */}
        {categories.length > 0 && (
          <section>
            <SectionHeader title="按品类选购" />
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              {categories.map((c) => (
                <Link
                  key={c}
                  href={`/shop/products?category=${encodeURIComponent(c)}`}
                  className="group/cat overflow-hidden rounded-xl ring-1 ring-foreground/10"
                >
                  <div className="relative aspect-square bg-muted">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={categoryImageUrl(c, 240, 240)}
                      alt={c}
                      className="size-full object-cover transition-transform duration-300 group-hover/cat:scale-105"
                    />
                    <div className="absolute inset-0 flex items-end bg-gradient-to-t from-black/60 to-transparent p-2">
                      <span className="text-sm font-medium text-white">{c}</span>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          </section>
        )}

        {/* 精选 */}
        <section>
          <SectionHeader
            eyebrow="高分好评"
            title="精选商品"
            action={
              <Link href="/shop/products" className="text-sm font-medium text-primary hover:underline">
                查看全部
              </Link>
            }
          />
          {products == null ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="aspect-[4/3] w-full rounded-xl" />
              ))}
            </div>
          ) : (
            <motion.div
              variants={reduce ? undefined : listStagger}
              initial={reduce ? undefined : "hidden"}
              animate={reduce ? undefined : "visible"}
              className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4"
            >
              {products.map((p) => (
                <motion.div key={p.id} variants={reduce ? undefined : listItem}>
                  <ProductGridCard product={p} />
                </motion.div>
              ))}
            </motion.div>
          )}
        </section>
      </div>
    </motion.div>
  );
}
