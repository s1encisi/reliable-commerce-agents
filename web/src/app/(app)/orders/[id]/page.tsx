"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter, useParams, useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api, apiUrl, ApiError } from "@/lib/api";
import { toastOrderCancelled, toastReturnInitiated } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  ArrowLeft,
  Package,
  Truck,
  Clock,
  CheckCircle,
  XCircle,
  RotateCcw,
  MapPin,
  Loader2,
  Download,
  Ban,
  AlertCircle,
} from "lucide-react";
import { productImageUrl } from "@/lib/images";
import { getReturnIntent, currentReturnIntent, clearReturnIntent } from "@/lib/return-intent";
import type { ReturnOperation } from "@/lib/return-operation";

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

interface StatusHistoryEntry {
  status: string;
  timestamp: string;
  notes?: string;
  location?: string;
}

interface OrderItem {
  product_id: string;
  name: string;
  category: string;
  image_url?: string;
  quantity: number;
  unit_price: number;
  subtotal: number;
}

interface ShippingInfo {
  street: string;
  city: string;
  state: string;
  zip: string;
  carrier?: string;
  tracking_number?: string;
}

interface ReturnInfo {
  id: string;
  reason: string;
  status: string;
  refund_method: string;
  refund_amount: number | null;
  created_at?: string;
  resolved_at?: string | null;
  return_label_url?: string;
}

interface BillingAddress {
  street: string;
  city: string;
  state: string;
  zip: string;
  name?: string;
  country?: string;
}

interface OrderDetail {
  id: string;
  date: string;
  status: string;
  items: OrderItem[];
  status_history: StatusHistoryEntry[];
  shipping_address: ShippingInfo;
  billing_address?: BillingAddress | null;
  carrier?: string;
  tracking?: string;
  discount: number;
  total: number;
  return?: ReturnInfo | null;
  [key: string]: unknown;
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

function formatTimestamp(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleString("zh-CN", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return dateStr;
  }
}

const STATUS_CONFIG: Record<
  string,
  { color: string; dotColor: string; icon: React.ElementType; label: string }
> = {
  placed: {
    color: "border-blue-200 bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300 dark:border-blue-500/30",
    dotColor: "bg-blue-500",
    icon: Clock,
    label: "已下单",
  },
  confirmed: {
    color: "border-indigo-200 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300 dark:border-indigo-500/30",
    dotColor: "bg-indigo-500",
    icon: CheckCircle,
    label: "已确认",
  },
  shipped: {
    color: "border-amber-200 bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300 dark:border-amber-500/30",
    dotColor: "bg-amber-500",
    icon: Truck,
    label: "已发货",
  },
  delivered: {
    color: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300 dark:border-emerald-500/30",
    dotColor: "bg-emerald-500",
    icon: CheckCircle,
    label: "已送达",
  },
  returned: {
    color: "border-orange-200 bg-orange-50 text-orange-700 dark:bg-orange-500/15 dark:text-orange-300 dark:border-orange-500/30",
    dotColor: "bg-orange-500",
    icon: RotateCcw,
    label: "已退货",
  },
  cancelled: {
    color: "border-red-200 bg-red-50 text-red-700 dark:bg-red-500/15 dark:text-red-300 dark:border-red-500/30",
    dotColor: "bg-red-500",
    icon: XCircle,
    label: "已取消",
  },
};

function getStatusConfig(status: string) {
  const key = status.toLowerCase();
  return (
    STATUS_CONFIG[key] || {
      color: "border-border bg-muted/50 text-muted-foreground",
      dotColor: "bg-muted-foreground",
      icon: Package,
      label: status,
    }
  );
}

// ---------------------------------------------------------------------------
// 状态时间线
// ---------------------------------------------------------------------------

function StatusTimeline({ history }: { history: StatusHistoryEntry[] }) {
  if (!history || history.length === 0) return null;

  return (
    <div className="relative space-y-0">
      {history.map((entry, idx) => {
        const isLatest = idx === history.length - 1;
        const isLast = idx === history.length - 1;
        const cfg = getStatusConfig(entry.status);

        return (
          <div key={idx} className="relative flex gap-4 pb-6 last:pb-0">
            {/* 竖线 */}
            {!isLast && (
              <div className="absolute left-[11px] top-6 h-[calc(100%-12px)] w-0.5 bg-border" />
            )}

            {/* 圆点 */}
            <div
              className={`relative z-10 mt-1 size-6 shrink-0 rounded-full border-2 ${
                isLatest
                  ? `${cfg.dotColor} border-card ring-2 ring-offset-1 ring-${cfg.dotColor}`
                  : "border-border bg-card"
              } flex items-center justify-center`}
            >
              {isLatest && (
                <div className="size-2 rounded-full bg-card" />
              )}
              {!isLatest && (
                <div className="size-2 rounded-full bg-muted-foreground" />
              )}
            </div>

            {/* 内容 */}
            <div className="flex-1 min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`text-sm font-medium ${
                    isLatest ? "text-foreground" : "text-muted-foreground"
                  }`}
                >
                  {cfg.label}
                </span>
                <span className="text-xs text-muted-foreground">
                  {formatTimestamp(entry.timestamp)}
                </span>
              </div>
              {entry.notes && (
                <p className="mt-0.5 text-sm text-muted-foreground">{entry.notes}</p>
              )}
              {entry.location && (
                <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
                  <MapPin className="size-3" />
                  {entry.location}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 骨架屏
// ---------------------------------------------------------------------------

function DetailSkeleton() {
  return (
    <div className="animate-pulse space-y-8">
      <div className="space-y-3">
        <div className="h-6 w-64 rounded bg-muted" />
        <div className="h-4 w-40 rounded bg-muted" />
      </div>
      <div className="grid gap-8 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <div className="h-48 rounded-xl bg-muted" />
        </div>
        <div className="space-y-4">
          <div className="h-40 rounded-xl bg-muted" />
          <div className="h-32 rounded-xl bg-muted" />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export default function OrderDetailPage() {
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const { user, isLoading: authLoading } = useAuth();

  const justPlaced = searchParams.get("placed") === "true";

  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 取消订单状态
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [cancelling, setCancelling] = useState(false);

  // 退货状态
  const [returnOpen, setReturnOpen] = useState(false);
  const [returnReason, setReturnReason] = useState("");
  const [refundMethod, setRefundMethod] = useState<"original_payment" | "store_credit">("original_payment");
  const [returning, setReturning] = useState(false);
  const [returnFeedback, setReturnFeedback] = useState("");
  const [returnOutcome, setReturnOutcome] = useState<string | undefined>();

  const orderId = params.id as string;

  const loadOrder = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getOrder(orderId);
      setOrder(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "订单加载失败"
      );
    } finally {
      setLoading(false);
    }
  }, [orderId]);

  useEffect(() => {
    if (user && orderId) loadOrder();
  }, [user, orderId, loadOrder]);

  const handleCancelOrder = async () => {
    if (!cancelReason.trim()) return;
    try {
      setCancelling(true);
      await api.cancelOrder(orderId, cancelReason);
      setCancelOpen(false);
      setCancelReason("");
      toastOrderCancelled(orderId);
      await loadOrder();
    } catch (err) {
      setError(err instanceof Error ? err.message : "取消订单失败");
    } finally {
      setCancelling(false);
    }
  };

  const showReturnResult = async (result: ReturnOperation) => {
    setReturnOutcome(result.outcome);
    setReturnFeedback(result.message ?? result.status ?? "已收到请求状态。");
    if (result.success !== false && result.return_id && (result.outcome === "SUCCEEDED" || result.status === "requested")) {
      toastReturnInitiated(result.return_id);
      setReturnOpen(false);
      await loadOrder();
    }
  };

  const handleReturnOrder = async () => {
    if (!returnReason.trim() || !user) return;
    try {
      setReturning(true);
      setReturnFeedback("");
      const id = await getReturnIntent(user.email, orderId, returnReason, refundMethod);
      await showReturnResult(await api.initiateReturn(orderId, returnReason, refundMethod, id));
    } catch (err) {
      setReturnFeedback(err instanceof Error ? err.message : "结果未确认。请在重试前查看请求状态。");
      if (err instanceof ApiError) setReturnOutcome(String(err.data.outcome ?? "UNKNOWN"));
    } finally {
      setReturning(false);
    }
  };

  const checkReturnStatus = async () => {
    if (!user) return;
    const id = currentReturnIntent(user.email, orderId);
    if (!id) { setReturnFeedback("该订单没有已保存的退货申请。"); return; }
    try {
      setReturning(true);
      await showReturnResult(await api.returnOperation(id));
    } catch (err) {
      setReturnFeedback(err instanceof Error ? err.message : "无法确认状态，请重试。");
    } finally { setReturning(false); }
  };

  if (authLoading || !user) return null;

  const statusCfg = order ? getStatusConfig(order.status) : null;

  return (
    <div className="min-h-screen bg-background">
      {/* 页头 */}
      <div className="border-b border-border bg-card">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          {/* 返回按钮 */}
          <Button
            variant="ghost"
            size="sm"
            className="mb-4 -ml-2 text-muted-foreground hover:text-muted-foreground"
            onClick={() => router.push("/orders")}
          >
            <ArrowLeft className="mr-1.5 size-4" />
            返回订单列表
          </Button>

          {loading && <DetailSkeleton />}
          {error && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {!loading && !error && order && statusCfg && (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-3">
                  <h1 className="text-2xl font-bold text-foreground">
                    订单 #{order.id}
                  </h1>
                  <Badge variant="outline" className={statusCfg.color}>
                    {statusCfg.label}
                  </Badge>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  下单时间 {formatDate(order.date)}
                </p>
              </div>

              {/* 取消/退货操作按钮 */}
              <div className="flex items-center gap-2">
                {(order.status === "placed" || order.status === "confirmed") && (
                  <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
                    <DialogTrigger
                      render={
                        <Button variant="outline" className="border-red-200 text-red-600 hover:bg-red-50 hover:text-red-700">
                          <Ban className="mr-1.5 size-4" />
                          取消订单
                        </Button>
                      }
                    />
                    <DialogContent>
                      <DialogHeader>
                        <DialogTitle>取消订单</DialogTitle>
                      </DialogHeader>
                      <div className="space-y-4 pt-2">
                        <div className="space-y-2">
                          <Label htmlFor="cancel-reason">取消原因</Label>
                          <Textarea
                            id="cancel-reason"
                            placeholder="请说明您取消该订单的原因…"
                            value={cancelReason}
                            onChange={(e) => setCancelReason(e.target.value)}
                            rows={3}
                          />
                        </div>
                        <Button
                          className="w-full bg-red-600 text-white hover:bg-red-700"
                          disabled={cancelling || !cancelReason.trim()}
                          onClick={handleCancelOrder}
                        >
                          {cancelling && <Loader2 className="mr-2 size-4 animate-spin" />}
                          确认取消
                        </Button>
                      </div>
                    </DialogContent>
                  </Dialog>
                )}

                {order.status === "delivered" && !order["return"] && (
                  <Dialog open={returnOpen} onOpenChange={setReturnOpen}>
                    <DialogTrigger
                      render={
                        <Button variant="outline" className="border-orange-200 text-orange-600 hover:bg-orange-50 hover:text-orange-700">
                          <RotateCcw className="mr-1.5 size-4" />
                          退货
                        </Button>
                      }
                    />
                    <DialogContent>
                      <DialogHeader>
                        <DialogTitle>退货</DialogTitle>
                      </DialogHeader>
                      <div className="space-y-4 pt-2">
                        <div className="space-y-2">
                          <Label htmlFor="return-reason">退货原因</Label>
                          <Textarea
                            id="return-reason"
                            maxLength={255}
                            placeholder="请说明您要退货的原因…"
                            value={returnReason}
                            onChange={(e) => setReturnReason(e.target.value)}
                            rows={3}
                          />
                        </div>
                        <div className="space-y-2">
                          <Label>退款方式</Label>
                          <div className="flex gap-2">
                            <Button
                              type="button"
                              variant={refundMethod === "original_payment" ? "default" : "outline"}
                              size="sm"
                              className={refundMethod === "original_payment" ? "bg-primary hover:opacity-90" : ""}
                              onClick={() => setRefundMethod("original_payment")}
                            >
                              原路退回
                            </Button>
                            <Button
                              type="button"
                              variant={refundMethod === "store_credit" ? "default" : "outline"}
                              size="sm"
                              className={refundMethod === "store_credit" ? "bg-primary hover:opacity-90" : ""}
                              onClick={() => setRefundMethod("store_credit")}
                            >
                              退至平台余额
                            </Button>
                          </div>
                        </div>
                        <Button
                          className="w-full bg-orange-600 text-white hover:bg-orange-700"
                          disabled={returning || !returnReason.trim()}
                          onClick={handleReturnOrder}
                        >
                          {returning && <Loader2 className="mr-2 size-4 animate-spin" />}
                          提交退货申请
                        </Button>
                        <Button variant="outline" disabled={returning} onClick={checkReturnStatus}>
                          查看请求状态
                        </Button>
                        {returnFeedback && <p role="status" className="text-sm text-muted-foreground">{returnFeedback}</p>}
                        {returnOutcome === "REJECTED" && (
                          <Button variant="ghost" disabled={returning} onClick={() => {
                            if (user) clearReturnIntent(user.email, orderId);
                            setReturnOutcome(undefined);
                            setReturnFeedback("新建申请将重新校验当前订单与退货政策。");
                          }}>发起新申请</Button>
                        )}
                      </div>
                    </DialogContent>
                  </Dialog>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 下单成功提示条 */}
      {justPlaced && !loading && !error && order && (
        <div className="border-b border-emerald-200 bg-emerald-50">
          <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
            <div className="flex items-center gap-3">
              <CheckCircle className="size-5 text-emerald-600 shrink-0" />
              <div>
                <p className="font-medium text-emerald-800">
                  下单成功！
                </p>
                <p className="text-sm text-emerald-600">
                  您的订单正在处理中。
                </p>
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="shrink-0 border-emerald-300 text-emerald-700 hover:bg-emerald-100"
              onClick={() => router.push("/products")}
            >
              继续购物
            </Button>
          </div>
        </div>
      )}

      {/* 内容区 */}
      {!loading && !error && order && (
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="grid gap-8 lg:grid-cols-3">
            {/* 左列：时间线与商品 */}
            <div className="space-y-8 lg:col-span-2">
              {/* 状态时间线 */}
              {order.status_history && order.status_history.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle>订单状态</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <StatusTimeline history={order.status_history} />
                  </CardContent>
                </Card>
              )}

              {/* 订单商品 */}
              <Card>
                <CardHeader>
                  <CardTitle>
                    商品（{order.items.length}）
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead className="w-12"></TableHead>
                        <TableHead>商品</TableHead>
                        <TableHead>品类</TableHead>
                        <TableHead className="text-right">数量</TableHead>
                        <TableHead className="text-right">
                          单价
                        </TableHead>
                        <TableHead className="text-right">
                          小计
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {order.items.map((item, idx) => (
                        <TableRow
                          key={idx}
                          className="cursor-pointer hover:bg-accent"
                          onClick={() =>
                            router.push(`/products/${item.product_id}`)
                          }
                        >
                          <TableCell>
                            <img
                              src={productImageUrl(item.product_id, 48, 48, item.image_url, item.category)}
                              alt={item.name}
                              className="size-10 rounded-md object-cover bg-muted"
                              loading="lazy"
                            />
                          </TableCell>
                          <TableCell className="font-medium text-foreground">
                            {item.name}
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant="outline"
                              className="text-[10px] font-normal"
                            >
                              {item.category}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-right">
                            {item.quantity}
                          </TableCell>
                          <TableCell className="text-right text-muted-foreground">
                            {formatPrice(item.unit_price)}
                          </TableCell>
                          <TableCell className="text-right font-medium">
                            {formatPrice(item.subtotal)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>

            {/* 右列：配送、摘要、退货 */}
            <div className="space-y-6">
              {/* 配送信息 */}
              {order.shipping_address && (
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <MapPin className="size-4 text-muted-foreground" />
                      配送信息
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="text-sm text-muted-foreground">
                      <p>{order.shipping_address.street}</p>
                      <p>
                        {order.shipping_address.city}, {order.shipping_address.state}{" "}
                        {order.shipping_address.zip}
                      </p>
                    </div>
                    {order.carrier && (
                      <>
                        <Separator />
                        <div className="space-y-1.5">
                          <div className="flex items-center gap-2 text-sm">
                            <Truck className="size-4 text-muted-foreground" />
                            <span className="font-medium text-muted-foreground">
                              {order.carrier}
                            </span>
                          </div>
                          {order.tracking && (
                            <p className="font-mono text-xs text-muted-foreground pl-6">
                              {order.tracking}
                            </p>
                          )}
                        </div>
                      </>
                    )}

                    {/* 账单地址 */}
                    <Separator />
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                        账单地址
                      </p>
                      {order.billing_address &&
                       (order.billing_address.street !== order.shipping_address.street ||
                        order.billing_address.city !== order.shipping_address.city ||
                        order.billing_address.state !== order.shipping_address.state ||
                        order.billing_address.zip !== order.shipping_address.zip) ? (
                        <div className="text-sm text-muted-foreground">
                          {order.billing_address.name && (
                            <p className="font-medium text-muted-foreground">{order.billing_address.name}</p>
                          )}
                          <p>{order.billing_address.street}</p>
                          <p>
                            {order.billing_address.city}, {order.billing_address.state}{" "}
                            {order.billing_address.zip}
                          </p>
                          {order.billing_address.country && (
                            <p>{order.billing_address.country}</p>
                          )}
                        </div>
                      ) : (
                        <p className="text-sm text-muted-foreground italic">
                          与收货地址相同
                        </p>
                      )}
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* 订单摘要 */}
              <Card>
                <CardHeader>
                  <CardTitle>订单摘要</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">小计</span>
                    <span className="text-muted-foreground">
                      {formatPrice(order.items?.reduce((sum: number, i: OrderItem) => sum + i.subtotal, 0) ?? 0)}
                    </span>
                  </div>
                  {order.discount > 0 && (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">优惠</span>
                      <span className="text-emerald-600">
                        -{formatPrice(order.discount)}
                      </span>
                    </div>
                  )}
                  <Separator />
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-muted-foreground">
                      合计
                    </span>
                    <span className="text-lg font-bold text-foreground">
                      {formatPrice(order.total)}
                    </span>
                  </div>
                </CardContent>
              </Card>

              {/* 退货信息 */}
              {order["return"] && (
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <RotateCcw className="size-4 text-orange-500" />
                      退货信息
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-muted-foreground">原因</span>
                        <span className="text-muted-foreground">
                          {(order["return"] as ReturnInfo).reason}
                        </span>
                      </div>
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-muted-foreground">状态</span>
                        <Badge
                          variant="outline"
                          className="border-orange-200 bg-orange-50 text-orange-700"
                        >
                          {(order["return"] as ReturnInfo).status}
                        </Badge>
                      </div>
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-muted-foreground">退款方式</span>
                        <span className="text-muted-foreground">
                          {(order["return"] as ReturnInfo).refund_method}
                        </span>
                      </div>
                      {(order["return"] as ReturnInfo).refund_amount != null && (
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-muted-foreground">退款金额</span>
                          <span className="font-medium text-emerald-600">
                            {formatPrice((order["return"] as ReturnInfo).refund_amount!)}
                          </span>
                        </div>
                      )}
                    </div>
                    {((order["return"] as ReturnInfo).created_at ||
                      (order["return"] as ReturnInfo).resolved_at) && (
                      <>
                        <Separator />
                        <div className="space-y-1.5">
                          {(order["return"] as ReturnInfo).created_at && (
                            <div className="flex items-center justify-between text-xs text-muted-foreground">
                              <span>退货发起时间</span>
                              <span>
                                {formatDate((order["return"] as ReturnInfo).created_at!)}
                              </span>
                            </div>
                          )}
                          {(order["return"] as ReturnInfo).resolved_at && (
                            <div className="flex items-center justify-between text-xs text-muted-foreground">
                              <span>退款完成时间</span>
                              <span>
                                {formatDate((order["return"] as ReturnInfo).resolved_at!)}
                              </span>
                            </div>
                          )}
                        </div>
                      </>
                    )}

                    {/* 下载退货面单 */}
                    {(order["return"] as ReturnInfo).return_label_url && (
                      <>
                        <Separator />
                        <div className="space-y-2">
                          <Button
                            variant="outline"
                            className="w-full border-orange-200 text-orange-700 hover:bg-orange-50"
                            onClick={() => {
                              const url = (order["return"] as ReturnInfo).return_label_url || "";
                              window.open(apiUrl(url), "_blank");
                            }}
                          >
                            <Download className="mr-2 size-4" />
                            下载退货面单
                          </Button>
                          <p className="text-xs text-muted-foreground text-center">
                            打印退货面单，并到任意快递网点寄回
                          </p>
                        </div>
                      </>
                    )}
                  </CardContent>
                </Card>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
