"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  BarChart3,
  Package,
  DollarSign,
  ShoppingCart,
  Star,
  ArrowRight,
  Loader2,
} from "lucide-react";
import { formatPrice, formatDate } from "@/lib/format";
import { OrderStatusBadge } from "@/components/status-badge";

export default function SellerDashboardPage() {
  const router = useRouter();
  const { user, isLoading: authLoading, isAdmin } = useAuth();

  const [stats, setStats] = useState<any>(null);
  const [orders, setOrders] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isSeller = user?.role === "seller" || isAdmin;

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [statsRes, ordersRes] = await Promise.all([
        api.getSellerStats(),
        api.getSellerOrders(),
      ]);
      setStats(statsRes);
      setOrders(ordersRes.orders);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "看板数据加载失败",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user && isSeller) loadData();
  }, [user, isSeller, loadData]);

  if (authLoading) return null;

  if (!user || !isSeller) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center">
          <Package className="mx-auto size-12 text-muted-foreground" />
          <h2 className="mt-4 text-lg font-semibold text-foreground">
            无权访问
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            商家看板仅对商家和管理员开放。
          </p>
        </div>
      </div>
    );
  }

  const recentOrders = orders.slice(0, 5);

  return (
    <div className="min-h-screen bg-background">
      {/* 页头 */}
      <div className="border-b border-border bg-card">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
              <BarChart3 className="size-5 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-foreground">
                商家看板
              </h1>
              <p className="text-sm text-muted-foreground">
                管理您的商品并跟踪订单
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {/* 加载中 */}
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {/* 错误提示 */}
        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {!loading && !error && stats && (
          <>
            {/* 汇总卡片 */}
            <div className="mb-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
              <Card>
                <CardHeader>
                  <CardDescription>商品总数</CardDescription>
                  <CardTitle className="text-3xl">
                    {stats.product_count}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <Link href="/seller/products">
                    <Button variant="outline" size="sm">
                      查看全部 <ArrowRight className="ml-1 size-3" />
                    </Button>
                  </Link>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardDescription>总销售额</CardDescription>
                  <CardTitle className="flex items-center gap-2 text-3xl">
                    <DollarSign className="size-6 text-green-600" />
                    {formatPrice(stats.total_revenue)}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground">
                    来自 {stats.order_count} 笔订单
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardDescription>收到订单</CardDescription>
                  <CardTitle className="flex items-center gap-2 text-3xl">
                    <ShoppingCart className="size-6 text-blue-600" />
                    {stats.order_count}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground">
                    包含您商品的订单
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardDescription>商品平均评分</CardDescription>
                  <CardTitle className="flex items-center gap-2 text-3xl">
                    {stats.avg_rating > 0 ? stats.avg_rating.toFixed(1) : "暂无"}
                    {stats.avg_rating > 0 && (
                      <Star className="size-6 fill-amber-400 text-amber-400" />
                    )}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground">
                    覆盖 {stats.product_count} 件商品
                  </p>
                </CardContent>
              </Card>
            </div>

            {/* 最近订单表格 */}
            <div>
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-lg font-semibold text-foreground">
                  最近订单
                </h2>
                <Link href="/seller/products">
                  <Button variant="outline" size="sm">
                    管理商品
                  </Button>
                </Link>
              </div>
              <Card>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>订单号</TableHead>
                        <TableHead>买家</TableHead>
                        <TableHead>状态</TableHead>
                        <TableHead>日期</TableHead>
                        <TableHead>商品数</TableHead>
                        <TableHead className="text-right">金额</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {recentOrders.map((order: any) => (
                        <TableRow key={order.id}>
                          <TableCell className="font-mono text-xs text-muted-foreground">
                            订单 #{order.id?.slice(0, 8)}
                          </TableCell>
                          <TableCell>
                            <div>
                              <p className="text-sm font-medium text-foreground">
                                {order.buyer_name}
                              </p>
                              <p className="text-xs text-muted-foreground">
                                {order.buyer_email}
                              </p>
                            </div>
                          </TableCell>
                          <TableCell>
                            <OrderStatusBadge status={order.status} />
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {formatDate(order.date)}
                          </TableCell>
                          <TableCell className="text-xs">
                            {order.item_count} 件商品
                          </TableCell>
                          <TableCell className="text-right font-medium">
                            {formatPrice(order.total)}
                          </TableCell>
                        </TableRow>
                      ))}
                      {recentOrders.length === 0 && (
                        <TableRow>
                          <TableCell
                            colSpan={6}
                            className="py-8 text-center text-sm text-muted-foreground"
                          >
                            暂无包含您商品的订单。
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
