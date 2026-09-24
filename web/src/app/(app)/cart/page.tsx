"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useCart } from "@/lib/cart-context";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import {
  toastCouponApplied,
  toastCouponFailed,
  toastCartRemoved,
  toastCartUpdated,
} from "@/lib/toast";
import { formatPrice } from "@/lib/format";
import { productImageUrl } from "@/lib/images";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import {
  ShoppingCart,
  Trash2,
  Plus,
  Minus,
  Tag,
  ArrowRight,
  Loader2,
  ShoppingBag,
  AlertTriangle,
} from "lucide-react";

// ---------------------------------------------------------------------------
// 骨架屏
// ---------------------------------------------------------------------------

function CartItemSkeleton() {
  return (
    <div className="flex gap-4 animate-pulse py-4">
      <div className="size-20 shrink-0 rounded-lg bg-muted" />
      <div className="flex-1 space-y-2">
        <div className="h-4 w-3/4 rounded bg-muted" />
        <div className="h-3 w-1/3 rounded bg-muted" />
        <div className="h-3 w-1/4 rounded bg-muted" />
      </div>
      <div className="h-4 w-16 rounded bg-muted" />
    </div>
  );
}

function SummarySkeleton() {
  return (
    <Card className="animate-pulse">
      <CardHeader>
        <div className="h-5 w-32 rounded bg-muted" />
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="h-4 w-full rounded bg-muted" />
        <div className="h-4 w-full rounded bg-muted" />
        <div className="h-px bg-muted" />
        <div className="h-6 w-full rounded bg-muted" />
        <div className="h-10 w-full rounded bg-muted" />
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export default function CartPage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();
  const { cart, itemCount, isLoading, updateItem, removeItem, refreshCart } =
    useCart();

  const [couponInput, setCouponInput] = useState("");
  const [couponLoading, setCouponLoading] = useState(false);
  const [couponError, setCouponError] = useState<string | null>(null);
  const [updatingItems, setUpdatingItems] = useState<Set<string>>(new Set());

  if (authLoading || !user) return null;

  // —— 优惠券处理 ——

  async function handleApplyCoupon() {
    const code = couponInput.trim();
    if (!code) return;
    setCouponLoading(true);
    setCouponError(null);
    try {
      await api.applyCoupon(code);
      await refreshCart();
      setCouponInput("");
      toastCouponApplied(code);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "优惠券使用失败";
      setCouponError(msg);
      toastCouponFailed(msg);
    } finally {
      setCouponLoading(false);
    }
  }

  async function handleRemoveCoupon() {
    setCouponLoading(true);
    setCouponError(null);
    try {
      await api.removeCoupon();
      await refreshCart();
    } catch (err) {
      setCouponError(
        err instanceof Error ? err.message : "优惠券移除失败"
      );
    } finally {
      setCouponLoading(false);
    }
  }

  // —— 数量处理 ——

  async function handleUpdateQty(itemId: string, newQty: number) {
    if (newQty < 1) return;
    setUpdatingItems((prev) => new Set(prev).add(itemId));
    try {
      await updateItem(itemId, newQty);
      toastCartUpdated();
    } finally {
      setUpdatingItems((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      });
    }
  }

  async function handleRemoveItem(itemId: string) {
    setUpdatingItems((prev) => new Set(prev).add(itemId));
    try {
      await removeItem(itemId);
      toastCartRemoved("商品已移除");
    } finally {
      setUpdatingItems((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      });
    }
  }

  // —— 空状态 ——

  if (!isLoading && (!cart || cart.items.length === 0)) {
    return (
      <div className="min-h-screen bg-background">
        <div className="border-b border-border bg-card">
          <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
                <ShoppingCart className="size-5 text-primary-foreground" />
              </div>
              <div>
                <h1 className="text-2xl font-bold text-foreground">
                  购物车
                </h1>
                <p className="text-sm text-muted-foreground">0 件商品</p>
              </div>
            </div>
          </div>
        </div>
        <div className="mx-auto max-w-7xl px-4 py-20 text-center sm:px-6 lg:px-8">
          <ShoppingBag className="mx-auto size-12 text-muted-foreground" />
          <h2 className="mt-4 text-lg font-semibold text-muted-foreground">
            购物车是空的
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            您还没有添加任何商品。
          </p>
          <Button
            className="mt-6 bg-primary hover:opacity-90"
            onClick={() => router.push("/products")}
          >
            浏览商品
          </Button>
        </div>
      </div>
    );
  }

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
                购物车
              </h1>
              <p className="text-sm text-muted-foreground">
                {isLoading ? "加载中…" : `${itemCount} 件商品`}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* 内容区 */}
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {isLoading ? (
          <div className="grid gap-8 lg:grid-cols-3">
            <div className="space-y-0 divide-y divide-border lg:col-span-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <CartItemSkeleton key={i} />
              ))}
            </div>
            <div>
              <SummarySkeleton />
            </div>
          </div>
        ) : (
          <div className="grid gap-8 lg:grid-cols-3">
            {/* 商品列表列 */}
            <div className="lg:col-span-2">
              <Card>
                <CardContent className="divide-y divide-border p-0">
                  {cart!.items.map((item) => {
                    const isUpdating = updatingItems.has(item.id);
                    const onSale =
                      item.original_price &&
                      item.original_price > item.price;
                    const lowStock =
                      item.in_stock !== false &&
                      item.available_qty != null &&
                      item.available_qty <= 5 &&
                      item.available_qty > 0;
                    const outOfStock = item.in_stock === false;

                    return (
                      <div
                        key={item.id}
                        className={`flex gap-4 p-4 sm:p-6 ${
                          isUpdating ? "opacity-60" : ""
                        }`}
                      >
                        {/* 商品图片 */}
                        <Link
                          href={`/products/${item.product_id}`}
                          className="shrink-0"
                        >
                          <img
                            src={productImageUrl(item.product_id, 80, 80, item.image_url, item.category)}
                            alt={item.name}
                            className="size-20 rounded-lg object-cover bg-muted"
                            loading="lazy"
                          />
                        </Link>

                        {/* 商品信息 */}
                        <div className="flex flex-1 flex-col gap-1 min-w-0">
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <Link
                                href={`/products/${item.product_id}`}
                                className="text-sm font-medium text-foreground hover:text-primary hover:underline line-clamp-1"
                              >
                                {item.name}
                              </Link>
                              <p className="text-xs text-muted-foreground">
                                {item.brand}
                              </p>
                            </div>
                            <Badge
                              variant="outline"
                              className="shrink-0 text-[10px] font-normal"
                            >
                              {item.category}
                            </Badge>
                          </div>

                          {/* 价格 */}
                          <div className="flex items-baseline gap-2">
                            <span className="text-sm font-medium text-foreground">
                              {formatPrice(item.price)}
                            </span>
                            {onSale && (
                              <span className="text-xs text-muted-foreground line-through">
                                {formatPrice(item.original_price!)}
                              </span>
                            )}
                          </div>

                          {/* 库存提醒 */}
                          {outOfStock && (
                            <div className="flex items-center gap-1 text-xs text-red-600">
                              <AlertTriangle className="size-3" />
                              缺货
                            </div>
                          )}
                          {lowStock && (
                            <div className="flex items-center gap-1 text-xs text-amber-600">
                              <AlertTriangle className="size-3" />
                              仅剩 {item.available_qty} 件
                            </div>
                          )}

                          {/* 数量控制与小计 */}
                          <div className="mt-auto flex items-center justify-between pt-2">
                            <div className="flex items-center gap-1">
                              <Button
                                variant="outline"
                                size="icon"
                                className="size-7"
                                disabled={isUpdating || item.quantity <= 1}
                                onClick={() =>
                                  handleUpdateQty(item.id, item.quantity - 1)
                                }
                              >
                                <Minus className="size-3" />
                              </Button>
                              <span className="w-8 text-center text-sm font-medium text-muted-foreground">
                                {item.quantity}
                              </span>
                              <Button
                                variant="outline"
                                size="icon"
                                className="size-7"
                                disabled={isUpdating}
                                onClick={() =>
                                  handleUpdateQty(item.id, item.quantity + 1)
                                }
                              >
                                <Plus className="size-3" />
                              </Button>

                              <Button
                                variant="ghost"
                                size="icon"
                                className="ml-2 size-7 text-muted-foreground hover:text-red-600"
                                disabled={isUpdating}
                                onClick={() => handleRemoveItem(item.id)}
                              >
                                <Trash2 className="size-3.5" />
                              </Button>
                            </div>

                            <span className="text-sm font-semibold text-foreground">
                              {formatPrice(item.subtotal)}
                            </span>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </CardContent>
              </Card>
            </div>

            {/* 摘要列 */}
            <div className="lg:sticky lg:top-8 lg:self-start">
              <Card>
                <CardHeader>
                  <CardTitle>订单摘要</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {/* 小计 */}
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">小计</span>
                    <span className="text-muted-foreground">
                      {formatPrice(cart!.subtotal)}
                    </span>
                  </div>

                  {/* 优惠券区域 */}
                  <div className="space-y-2">
                    {cart!.coupon_code ? (
                      <div className="flex items-center justify-between rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2">
                        <div className="flex items-center gap-2">
                          <Tag className="size-3.5 text-emerald-600" />
                          <Badge
                            variant="outline"
                            className="border-emerald-300 bg-emerald-100 text-emerald-700"
                          >
                            {cart!.coupon_code}
                          </Badge>
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-auto px-2 py-1 text-xs text-muted-foreground hover:text-red-600"
                          disabled={couponLoading}
                          onClick={handleRemoveCoupon}
                        >
                          {couponLoading ? (
                            <Loader2 className="size-3 animate-spin" />
                          ) : (
                            "移除"
                          )}
                        </Button>
                      </div>
                    ) : (
                      <div className="flex gap-2">
                        <Input
                          placeholder="优惠码"
                          value={couponInput}
                          onChange={(e) => {
                            setCouponInput(e.target.value);
                            setCouponError(null);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleApplyCoupon();
                          }}
                          className="h-9 text-sm"
                        />
                        <Button
                          variant="outline"
                          size="sm"
                          className="shrink-0"
                          disabled={couponLoading || !couponInput.trim()}
                          onClick={handleApplyCoupon}
                        >
                          {couponLoading ? (
                            <Loader2 className="size-3 animate-spin" />
                          ) : (
                            "使用"
                          )}
                        </Button>
                      </div>
                    )}
                    {couponError && (
                      <p className="text-xs text-red-600">{couponError}</p>
                    )}
                  </div>

                  {/* 优惠金额 */}
                  {cart!.discount_amount > 0 && (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">优惠</span>
                      <span className="text-emerald-600">
                        -{formatPrice(cart!.discount_amount)}
                      </span>
                    </div>
                  )}

                  <Separator />

                  {/* 合计 */}
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-muted-foreground">
                      合计
                    </span>
                    <span className="text-xl font-bold text-foreground">
                      {formatPrice(cart!.total)}
                    </span>
                  </div>

                  {/* 结算按钮 */}
                  <Button
                    className="w-full bg-primary hover:opacity-90"
                    size="lg"
                    onClick={() => router.push("/checkout")}
                  >
                    去结算
                    <ArrowRight className="ml-2 size-4" />
                  </Button>

                  {/* 继续购物 */}
                  <div className="text-center">
                    <Link
                      href="/products"
                      className="text-sm text-muted-foreground hover:text-primary hover:underline"
                    >
                      继续购物
                    </Link>
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
