import {
  LayoutDashboard,
  MessageSquare,
  ShoppingBag,
  ShoppingCart,
  Package,
  User,
  BarChart3,
  Shield,
  Bot,
  CheckCircle,
  Activity,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  adminOnly?: boolean;
  sellerOnly?: boolean;
  /** 是否在购物车数量徽章中展示。 */
  cartBadge?: boolean;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

/**
 * 分组导航，由侧边栏与命令面板共用。
 * 分组思路受 WorkGraph 启发（工作区 / 智能体 / …），并按购物场景做了调整。
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "购物",
    items: [
      { label: "首页", href: "/home", icon: LayoutDashboard },
      { label: "对话", href: "/chat", icon: MessageSquare },
      { label: "智能体", href: "/agents", icon: Bot },
      { label: "商品", href: "/products", icon: ShoppingBag },
      { label: "购物车", href: "/cart", icon: ShoppingCart, cartBadge: true },
      { label: "订单", href: "/orders", icon: Package },
      { label: "运行记录", href: "/runs", icon: Activity },
    ],
  },
  {
    label: "账户",
    items: [
      { label: "个人中心", href: "/profile", icon: User },
      { label: "商家", href: "/seller", icon: BarChart3, sellerOnly: true },
    ],
  },
  {
    label: "管理",
    items: [
      { label: "概览", href: "/admin", icon: Shield, adminOnly: true },
      { label: "审批", href: "/admin/approvals", icon: CheckCircle, adminOnly: true },
      { label: "用量统计", href: "/admin/usage", icon: BarChart3, adminOnly: true },
    ],
  },
];

/** 按当前用户的角色标识过滤导航项。 */
export function visibleGroups(opts: {
  isAdmin: boolean;
  isSeller: boolean;
}): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => {
      if (item.adminOnly) return opts.isAdmin;
      if (item.sellerOnly) return opts.isSeller;
      return true;
    }),
  })).filter((group) => group.items.length > 0);
}

/** 当前路径对应的扁平化标签，供顶栏面包屑使用。 */
export function labelForPath(pathname: string): string {
  const flat = NAV_GROUPS.flatMap((g) => g.items);
  // 匹配最长的 href（因此 /admin/usage 优先于 /admin）。
  const match = flat
    .filter((i) => pathname === i.href || pathname.startsWith(i.href + "/"))
    .sort((a, b) => b.href.length - a.href.length)[0];
  return match?.label ?? "首页";
}
