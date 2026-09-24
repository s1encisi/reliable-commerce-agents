"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { CartProvider } from "@/lib/cart-context";
import { DesktopSidebar, MobileSidebar } from "@/components/sidebar";
import { TopBar } from "@/components/top-bar";
import { CommandPalette } from "@/components/command-palette";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !user) {
      router.replace("/login");
    }
  }, [user, isLoading, router]);

  // 校验登录状态期间不渲染任何内容
  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="size-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  // 确认用户已登录前，不渲染应用外壳
  if (!user) {
    return null;
  }

  return (
    <CartProvider>
      <CommandPalette />
      <div className="flex h-screen overflow-hidden">
        <DesktopSidebar />
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* 移动端顶栏 */}
          <header className="flex h-14 items-center gap-2 border-b bg-background px-4 lg:hidden">
            <MobileSidebar />
            <span className="text-sm font-semibold">可靠电商多智能体平台</span>
          </header>
          {/* 桌面端顶栏 */}
          <TopBar />
          {/* 主内容区 */}
          <main className="flex-1 overflow-y-auto">{children}</main>
        </div>
      </div>
    </CartProvider>
  );
}
