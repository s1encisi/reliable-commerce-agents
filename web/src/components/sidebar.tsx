"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Store, LogOut, Menu } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  Sheet,
  SheetContent,
  SheetTrigger,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { visibleGroups, type NavItem } from "@/lib/nav";

/** 侧边栏中的单个导航项，激活态由 `isActive` 控制。 */
function NavLink({
  item,
  isActive,
  onClick,
}: {
  item: NavItem;
  isActive: boolean;
  onClick?: () => void;
}) {
  const Icon = item.icon;

  return (
    <Link
      href={item.href}
      onClick={onClick}
      aria-current={isActive ? "page" : undefined}
      className={cn(
        "group/navlink flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
        isActive
          ? "bg-primary/10 text-primary"
          : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
      )}
    >
      <Icon
        className={cn(
          "size-4 shrink-0 transition-colors",
          isActive
            ? "text-primary"
            : "text-muted-foreground/70 group-hover/navlink:text-accent-foreground",
        )}
      />
      <span className="flex-1">{item.label}</span>
      {item.cartBadge && <CartBadge />}
    </Link>
  );
}

/** 购物车条目数角标；数量为 0 时不渲染。 */
function CartBadge() {
  const { itemCount } = useCart();
  if (itemCount === 0) return null;
  return (
    <span className="flex size-5 items-center justify-center rounded-full bg-primary text-[10px] font-bold text-primary-foreground">
      {itemCount > 99 ? "99+" : itemCount}
    </span>
  );
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { user, logout, isAdmin } = useAuth();
  const pathname = usePathname();

  const isSeller = user?.role === "seller" || isAdmin;
  const groups = visibleGroups({ isAdmin, isSeller });

  // 只让最精确匹配的导航项处于激活态，这样在 /admin/usage 下时，父级路由
  // /admin 不会同时高亮。
  const activeHref = groups
    .flatMap((g) => g.items)
    .filter((i) => pathname === i.href || pathname.startsWith(i.href + "/"))
    .sort((a, b) => b.href.length - a.href.length)[0]?.href;

  const initials = user?.name
    ? user.name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2)
    : "U";

  return (
    <div className="flex h-full flex-col">
      {/* 品牌区 */}
      <div className="flex h-14 items-center gap-2 px-4">
        <div className="flex size-8 items-center justify-center rounded-lg bg-primary">
          <Store className="size-4 text-primary-foreground" />
        </div>
        <span className="text-sm font-semibold tracking-tight">
          可靠电商多智能体平台
        </span>
      </div>

      <Separator />

      {/* 导航区 */}
      <ScrollArea className="flex-1 px-3 py-4">
        <nav className="flex flex-col gap-5">
          {groups.map((group) => (
            <div key={group.label} className="flex flex-col gap-1">
              <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60">
                {group.label}
              </p>
              {group.items.map((item) => (
                <NavLink
                  key={item.href}
                  item={item}
                  isActive={item.href === activeHref}
                  onClick={onNavigate}
                />
              ))}
            </div>
          ))}
        </nav>
      </ScrollArea>

      {/* 用户区 */}
      <Separator />
      <div className="p-3">
        <Link
          href="/profile"
          onClick={onNavigate}
          className="flex items-center gap-3 rounded-lg px-3 py-2 transition-colors hover:bg-accent"
        >
          <Avatar className="size-8">
            <AvatarFallback className="text-xs">{initials}</AvatarFallback>
          </Avatar>
          <div className="flex-1 overflow-hidden">
            <p className="truncate text-sm font-medium">{user?.name}</p>
            <p className="truncate text-xs text-muted-foreground">
              {user?.email}
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              logout();
              onNavigate?.();
            }}
            aria-label="退出登录"
          >
            <LogOut className="size-4" />
          </Button>
        </Link>
      </div>
    </div>
  );
}

/** 桌面端固定侧边栏 */
export function DesktopSidebar() {
  return (
    <aside className="hidden w-64 shrink-0 border-r border-sidebar-border bg-sidebar lg:block">
      <SidebarContent />
    </aside>
  );
}

/** 移动端抽屉式侧边栏 */
export function MobileSidebar() {
  return (
    <Sheet>
      <SheetTrigger
        render={<Button variant="ghost" size="icon" className="lg:hidden" />}
      >
        <Menu className="size-5" />
        <span className="sr-only">切换菜单</span>
      </SheetTrigger>
      <SheetContent side="left" showCloseButton={false} className="w-64 p-0">
        <SheetTitle className="sr-only">导航</SheetTitle>
        <SidebarContent />
      </SheetContent>
    </Sheet>
  );
}
