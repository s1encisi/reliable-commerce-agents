"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import {
  ShoppingCart,
  Package,
  Truck,
  ChevronRight,
  Loader2,
  Clock,
  CheckCircle,
  XCircle,
  RotateCcw,
} from "lucide-react";

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

interface Order {
  id: string;
  date: string;
  status: string;
  item_count: number;
  total: number;
  carrier?: string;
  tracking?: string;
}

// ---------------------------------------------------------------------------
// 辅助函数
// ---------------------------------------------------------------------------

function formatPrice(price: number): string {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
  }).format(price);
}

function formatDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

const STATUS_CONFIG: Record<
  string,
  { color: string; icon: React.ElementType; label: string }
> = {
  placed: {
    color: "border-blue-200 bg-blue-50 text-blue-700",
    icon: Clock,
    label: "已下单",
  },
  confirmed: {
    color: "border-indigo-200 bg-indigo-50 text-indigo-700",
    icon: CheckCircle,
    label: "已确认",
  },
  shipped: {
    color: "border-amber-200 bg-amber-50 text-amber-700",
    icon: Truck,
    label: "已发货",
  },
  delivered: {
    color: "border-emerald-200 bg-emerald-50 text-emerald-700",
    icon: CheckCircle,
    label: "已送达",
  },
  returned: {
    color: "border-orange-200 bg-orange-50 text-orange-700",
    icon: RotateCcw,
    label: "已退货",
  },
  cancelled: {
    color: "border-red-200 bg-red-50 text-red-700",
    icon: XCircle,
    label: "已取消",
  },
};

function getStatusConfig(status: string) {
  const key = status.toLowerCase();
  return (
    STATUS_CONFIG[key] || {
      color: "border-border bg-muted/50 text-muted-foreground",
      icon: Package,
      label: status,
    }
  );
}

// ---------------------------------------------------------------------------
// 状态筛选标签
// ---------------------------------------------------------------------------

const STATUS_TABS = [
  { value: "", label: "全部" },
  { value: "placed", label: "已下单" },
  { value: "confirmed", label: "已确认" },
  { value: "shipped", label: "已发货" },
  { value: "delivered", label: "已送达" },
  { value: "returned", label: "已退货" },
  { value: "cancelled", label: "已取消" },
];

// ---------------------------------------------------------------------------
// 骨架屏
// ---------------------------------------------------------------------------

function OrderCardSkeleton() {
  return (
    <Card className="animate-pulse">
      <CardContent className="space-y-3 py-4">
        <div className="flex items-center justify-between">
          <div className="h-4 w-32 rounded bg-muted" />
          <div className="h-5 w-20 rounded-full bg-muted" />
        </div>
        <div className="h-3 w-48 rounded bg-muted" />
        <div className="flex items-center justify-between">
          <div className="h-4 w-24 rounded bg-muted" />
          <div className="h-4 w-16 rounded bg-muted" />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export default function OrdersPage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();

  const [orders, setOrders] = useState<Order[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeStatus, setActiveStatus] = useState("");

  const loadOrders = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getOrders(activeStatus || undefined);
      setOrders(data.orders);
      setTotal(data.total);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "订单加载失败"
      );
    } finally {
      setLoading(false);
    }
  }, [activeStatus]);

  useEffect(() => {
    if (user) loadOrders();
  }, [user, loadOrders]);

  if (authLoading || !user) return null;

  return (
    <div className="min-h-screen bg-background">
      {/* 页头 */}
      <div className="border-b border-border bg-card">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
              <ShoppingCart className="size-5 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-foreground">
                订单记录
              </h1>
              <p className="text-sm text-muted-foreground">
                查看并跟踪您的全部订单
              </p>
            </div>
          </div>

          {/* 状态筛选标签 */}
          <div className="mt-6 flex flex-wrap gap-2">
            {STATUS_TABS.map((tab) => (
              <Button
                key={tab.value}
                variant={activeStatus === tab.value ? "default" : "outline"}
                size="sm"
                className={
                  activeStatus === tab.value
                    ? "bg-primary text-primary-foreground hover:opacity-90"
                    : ""
                }
                onClick={() => setActiveStatus(tab.value)}
              >
                {tab.label}
              </Button>
            ))}
          </div>
        </div>
      </div>

      {/* 内容区 */}
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {/* 订单总数 */}
        {!loading && !error && orders.length > 0 && (
          <p className="mb-6 text-sm text-muted-foreground">共 {total} 笔订单</p>
        )}

        {/* 加载中 */}
        {loading && (
          <div className="space-y-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <OrderCardSkeleton key={i} />
            ))}
          </div>
        )}

        {/* 错误提示 */}
        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {/* 空状态 */}
        {!loading && !error && orders.length === 0 && (
          <div className="py-20 text-center">
            <ShoppingCart className="mx-auto size-10 text-muted-foreground" />
            <p className="mt-3 text-sm text-muted-foreground">
              {activeStatus
                ? `没有${STATUS_TABS.find((t) => t.value === activeStatus)?.label ?? activeStatus}的订单。`
                : "暂无订单。"}
            </p>
            {activeStatus && (
              <Button
                variant="outline"
                size="sm"
                className="mt-4"
                onClick={() => setActiveStatus("")}
              >
                查看全部订单
              </Button>
            )}
          </div>
        )}

        {/* 订单列表 */}
        {!loading && !error && orders.length > 0 && (
          <div className="space-y-4">
            {orders.map((order) => {
              const statusCfg = getStatusConfig(order.status);
              const StatusIcon = statusCfg.icon;

              return (
                <Card
                  key={order.id}
                  className="cursor-pointer transition-shadow hover:shadow-md"
                  onClick={() => router.push(`/orders/${order.id}`)}
                >
                  <CardContent className="py-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      {/* 左侧信息 */}
                      <div className="flex-1 space-y-2">
                        <div className="flex flex-wrap items-center gap-3">
                          <span className="font-mono text-sm font-medium text-foreground">
                            订单 #{order.id}
                          </span>
                          <Badge
                            variant="outline"
                            className={statusCfg.color}
                          >
                            <StatusIcon className="mr-1 size-3" />
                            {statusCfg.label}
                          </Badge>
                        </div>

                        <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
                          <span>{formatDate(order.date)}</span>
                          <span>
                            {order.item_count} 件商品
                          </span>
                          {order.carrier && (
                            <span className="flex items-center gap-1">
                              <Truck className="size-3.5" />
                              {order.carrier}
                              {order.tracking && (
                                <span className="font-mono text-xs">
                                  {order.tracking}
                                </span>
                              )}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* 右侧金额 */}
                      <div className="flex items-center gap-3">
                        <span className="text-lg font-bold text-foreground">
                          {formatPrice(order.total)}
                        </span>
                        <ChevronRight className="size-5 text-muted-foreground" />
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
