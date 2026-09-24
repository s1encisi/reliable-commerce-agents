"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Store, Search, Sparkles } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { CartProvider } from "@/lib/cart-context";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { ThemeToggle } from "@/components/ui/theme-toggle";

/**
 * 公开店铺外壳（无登录校验）。页头随登录状态切换：
 * 未登录显示「登录」，已登录显示账号头像。页脚回链项目主页。
 */
export default function ShopLayout({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const router = useRouter();
  const [q, setQ] = useState("");

  const initials = user?.name
    ? (() => {
        const parts = user.name.trim().split(/\s+/).filter(Boolean);
        return parts.length <= 1
          ? (parts[0] ?? "").slice(0, 2)
          : parts.map((n) => n[0]).join("").toUpperCase().slice(0, 2);
      })()
    : "用";

  function onSearch(e: FormEvent) {
    e.preventDefault();
    const query = q.trim();
    router.push(query ? `/shop/products?search=${encodeURIComponent(query)}` : "/shop/products");
  }

  return (
    <CartProvider>
    <div className="flex min-h-screen flex-col bg-background">
      <header className="sticky top-0 z-40 border-b bg-background/80 backdrop-blur supports-backdrop-filter:bg-background/60">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4 sm:px-6">
          <Link href="/shop" className="flex items-center gap-2">
            <div className="flex size-8 items-center justify-center rounded-lg bg-primary">
              <Store className="size-4 text-primary-foreground" />
            </div>
            <span className="hidden text-sm font-semibold tracking-tight sm:inline">
              可靠电商多智能体平台
            </span>
          </Link>

          <form onSubmit={onSearch} className="ml-2 flex flex-1 items-center">
            <div className="relative w-full max-w-md">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="搜索商品…"
                aria-label="搜索商品"
                className="h-9 w-full rounded-lg border bg-muted/40 pl-9 pr-3 text-sm outline-none transition-colors focus:border-ring focus:bg-background"
              />
            </div>
          </form>

          <nav className="flex items-center gap-1.5">
            <Button render={<Link href="/shop/products" />} variant="ghost" size="sm">
              商品
            </Button>
            <ThemeToggle />
            {user ? (
              <Link href="/home" aria-label="您的账号" className="rounded-full focus-visible:ring-2 focus-visible:ring-ring">
                <Avatar className="size-8">
                  <AvatarFallback className="text-xs">{initials}</AvatarFallback>
                </Avatar>
              </Link>
            ) : (
              <Button render={<Link href="/login" />} size="sm">
                登录
              </Button>
            )}
          </nav>
        </div>
      </header>

      <main className="flex-1">{children}</main>

      <footer className="border-t">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-3 px-4 py-6 text-sm text-muted-foreground sm:flex-row sm:px-6">
          <p className="flex items-center gap-1.5">
            <Sparkles className="size-3.5 text-primary" />
            智能体购物演示 · 由 6 个专业智能体驱动
          </p>
          <div className="flex items-center gap-4">
            <Link href="/" className="hover:text-foreground">关于本项目</Link>
            <Link
              href="https://github.com/s1encisi/reliable-commerce-agents"
              className="hover:text-foreground"
            >
              GitHub
            </Link>
          </div>
        </div>
      </footer>
    </div>
    </CartProvider>
  );
}
