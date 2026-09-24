"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter, useParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api } from "@/lib/api";
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
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  Star,
  ArrowLeft,
  Package,
  CheckCircle,
  XCircle,
  MessageSquare,
  ChevronRight,
  Loader2,
  MapPin,
  ShoppingCart,
  Check,
  Minus,
  Plus,
} from "lucide-react";
import { productImageUrl } from "@/lib/images";

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

interface Review {
  id: string;
  reviewer: string;
  rating: number;
  title: string;
  body: string;
  date: string;
  verified: boolean;
}

interface WarehouseStock {
  name: string;
  region: string;
  quantity: number;
}

interface ProductDetail {
  id: string;
  name: string;
  brand: string;
  category: string;
  price: number;
  original_price?: number;
  image_url?: string;
  rating: number;
  review_count: number;
  description: string;
  specs?: Record<string, string>;
  in_stock: boolean;
  total_stock: number;
  warehouses?: WarehouseStock[];
  rating_distribution?: Record<string, number>;
  reviews?: Review[];
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

function StarRating({ rating, count }: { rating: number; count: number }) {
  const fullStars = Math.floor(rating);
  const hasHalf = rating - fullStars >= 0.5;
  const emptyStars = 5 - fullStars - (hasHalf ? 1 : 0);

  return (
    <div className="flex items-center gap-1.5">
      <div className="flex items-center">
        {Array.from({ length: fullStars }).map((_, i) => (
          <Star
            key={`full-${i}`}
            className="size-4 fill-amber-400 text-amber-400"
          />
        ))}
        {hasHalf && (
          <div className="relative">
            <Star className="size-4 text-border" />
            <div className="absolute inset-0 overflow-hidden" style={{ width: "50%" }}>
              <Star className="size-4 fill-amber-400 text-amber-400" />
            </div>
          </div>
        )}
        {Array.from({ length: emptyStars }).map((_, i) => (
          <Star key={`empty-${i}`} className="size-4 text-border" />
        ))}
      </div>
      <span className="text-sm text-muted-foreground">
        {rating.toFixed(1)}（{count} 条评价）
      </span>
    </div>
  );
}

function SmallStarRating({ rating }: { rating: number }) {
  return (
    <div className="flex items-center">
      {Array.from({ length: 5 }).map((_, i) => (
        <Star
          key={i}
          className={`size-3.5 ${
            i < rating
              ? "fill-amber-400 text-amber-400"
              : "text-border"
          }`}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 评分分布条形图
// ---------------------------------------------------------------------------

function RatingDistribution({
  distribution,
}: {
  distribution: Record<string, number>;
}) {
  const entries = [5, 4, 3, 2, 1].map((star) => ({
    star,
    count: distribution[String(star)] || 0,
  }));
  const maxCount = Math.max(...entries.map((e) => e.count), 1);

  return (
    <div className="space-y-2">
      {entries.map(({ star, count }) => (
        <div key={star} className="flex items-center gap-3">
          <span className="w-8 text-right text-sm text-muted-foreground">
            {star}
          </span>
          <Star className="size-3.5 fill-amber-400 text-amber-400" />
          <div className="flex-1">
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-amber-400 transition-all"
                style={{ width: `${(count / maxCount) * 100}%` }}
              />
            </div>
          </div>
          <span className="w-8 text-sm text-muted-foreground">{count}</span>
        </div>
      ))}
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
        <div className="h-4 w-48 rounded bg-muted" />
        <div className="h-8 w-96 rounded bg-muted" />
        <div className="h-4 w-32 rounded bg-muted" />
      </div>
      <div className="grid gap-8 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <div className="h-4 w-full rounded bg-muted" />
          <div className="h-4 w-5/6 rounded bg-muted" />
          <div className="h-4 w-3/4 rounded bg-muted" />
        </div>
        <div className="space-y-3">
          <div className="h-32 rounded-xl bg-muted" />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export default function ProductDetailPage() {
  const router = useRouter();
  const params = useParams();
  const { user, isLoading: authLoading } = useAuth();

  const { addItem } = useCart();

  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [qty, setQty] = useState(1);
  const [added, setAdded] = useState(false);
  const [addingToCart, setAddingToCart] = useState(false);

  const productId = params.id as string;

  const loadProduct = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getProduct(productId);
      setProduct(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "商品加载失败"
      );
    } finally {
      setLoading(false);
    }
  }, [productId]);

  useEffect(() => {
    if (user && productId) loadProduct();
  }, [user, productId, loadProduct]);

  if (authLoading || !user) return null;

  const onSale =
    product?.original_price && product.original_price > product.price;
  const savePct = onSale
    ? Math.round(
        ((product.original_price! - product.price) / product.original_price!) *
          100
      )
    : 0;

  return (
    <div className="min-h-screen bg-background">
      {/* 页头 */}
      <div className="border-b border-border bg-card">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          {/* 返回按钮 */}
          <Button
            variant="ghost"
            size="sm"
            className="mb-4 -ml-2 text-muted-foreground hover:text-foreground"
            onClick={() => router.push("/products")}
          >
            <ArrowLeft className="mr-1.5 size-4" />
            返回商品列表
          </Button>

          {loading && <DetailSkeleton />}
          {error && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {!loading && !error && product && (
            <>
              {/* 面包屑 */}
              <nav className="mb-4 flex items-center gap-1.5 text-sm text-muted-foreground">
                <button
                  className="hover:text-primary transition-colors"
                  onClick={() => router.push("/products")}
                >
                  商品
                </button>
                <ChevronRight className="size-3.5" />
                <span>{product.category}</span>
                <ChevronRight className="size-3.5" />
                <span className="text-foreground">{product.name}</span>
              </nav>

              {/* 商品主区：图片与信息 */}
              <div className="grid gap-8 lg:grid-cols-2">
                {/* 图片 */}
                <div className="relative aspect-square overflow-hidden rounded-2xl bg-muted">
                  <img
                    src={productImageUrl(product.id, 800, 800, product.image_url, product.category)}
                    alt={product.name}
                    className="h-full w-full object-cover"
                  />
                  {product.original_price && product.original_price > product.price && (
                    <span className="absolute top-4 left-4 rounded-lg bg-red-500 px-3 py-1 text-sm font-bold text-white shadow-lg">
                      省 {Math.round((1 - product.price / product.original_price) * 100)}%
                    </span>
                  )}
                </div>

                {/* 商品信息 */}
                <div>
                  <div className="flex items-center gap-3">
                    <h1 className="text-2xl font-bold text-foreground">
                      {product.name}
                    </h1>
                    <Badge
                      variant="outline"
                      className="bg-muted text-muted-foreground border-border"
                    >
                      {product.category}
                    </Badge>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">{product.brand}</p>
                  <div className="mt-3">
                    <StarRating
                      rating={product.rating}
                      count={product.review_count}
                    />
                  </div>

                  <div className="mt-6 flex items-baseline gap-2">
                    <span className="text-3xl font-bold text-foreground">
                      {formatPrice(product.price)}
                    </span>
                    {onSale && (
                      <span className="text-lg text-muted-foreground line-through">
                        {formatPrice(product.original_price!)}
                      </span>
                    )}
                  </div>
                  {onSale && (
                    <Badge className="mt-2 bg-emerald-100 text-emerald-700 border-emerald-200">
                      省 {savePct}%
                    </Badge>
                  )}

                  <p className="mt-6 leading-relaxed text-muted-foreground">
                    {product.description}
                  </p>

                  <div className="mt-6">
                    {product.in_stock ? (
                      <div className="flex items-center gap-2">
                        <CheckCircle className="size-5 text-emerald-500" />
                        <span className="font-medium text-emerald-700">
                          有货（{product.total_stock} 件）
                        </span>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                        <XCircle className="size-5 text-red-500" />
                        <span className="font-medium text-red-700">
                          缺货
                        </span>
                      </div>
                    )}
                  </div>

                  {/* 加入购物车 */}
                  {product.in_stock && (
                    <div className="mt-6 space-y-3">
                      <div className="flex items-center gap-3">
                        <Button
                          variant="outline"
                          size="icon"
                          className="size-9"
                          disabled={qty <= 1}
                          onClick={() => setQty((q) => Math.max(1, q - 1))}
                        >
                          <Minus className="size-4" />
                        </Button>
                        <span className="w-8 text-center text-lg font-semibold text-foreground">
                          {qty}
                        </span>
                        <Button
                          variant="outline"
                          size="icon"
                          className="size-9"
                          disabled={qty >= 10}
                          onClick={() => setQty((q) => Math.min(10, q + 1))}
                        >
                          <Plus className="size-4" />
                        </Button>
                      </div>
                      <Button
                        className={`w-full text-base py-5 ${
                          added
                            ? "bg-emerald-600 hover:bg-emerald-700 text-white"
                            : "bg-primary hover:opacity-90 text-primary-foreground"
                        }`}
                        disabled={addingToCart}
                        onClick={async () => {
                          try {
                            setAddingToCart(true);
                            await addItem(product.id, qty);
                            setAdded(true);
                            setTimeout(() => setAdded(false), 2000);
                          } finally {
                            setAddingToCart(false);
                          }
                        }}
                      >
                        {addingToCart ? (
                          <Loader2 className="mr-2 size-5 animate-spin" />
                        ) : added ? (
                          <Check className="mr-2 size-5" />
                        ) : (
                          <ShoppingCart className="mr-2 size-5" />
                        )}
                        {added ? "已加入购物车！" : "加入购物车"}
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* 内容区 */}
      {!loading && !error && product && (
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="grid gap-8 lg:grid-cols-3">
            {/* 左列：描述、规格、评价 */}
            <div className="space-y-8 lg:col-span-2">
              {/* 商品描述 */}
              <Card>
                <CardHeader>
                  <CardTitle>商品描述</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="leading-relaxed text-muted-foreground">
                    {product.description}
                  </p>
                </CardContent>
              </Card>

              {/* 规格参数 */}
              {product.specs &&
                Object.keys(product.specs).length > 0 && (
                  <Card>
                    <CardHeader>
                      <CardTitle>规格参数</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <Table>
                        <TableBody>
                          {Object.entries(product.specs).map(
                            ([key, value]) => (
                              <TableRow key={key}>
                                <TableCell className="w-1/3 font-medium text-foreground">
                                  {key}
                                </TableCell>
                                <TableCell className="text-muted-foreground">
                                  {value}
                                </TableCell>
                              </TableRow>
                            )
                          )}
                        </TableBody>
                      </Table>
                    </CardContent>
                  </Card>
                )}

              {/* 评分分布 */}
              {product.rating_distribution && (
                <Card>
                  <CardHeader>
                    <CardTitle>评分分布</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <RatingDistribution
                      distribution={product.rating_distribution}
                    />
                  </CardContent>
                </Card>
              )}

              {/* 用户评价 */}
              {product.reviews && product.reviews.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle>
                      用户评价（{product.reviews.length}）
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {product.reviews.map((review, idx) => (
                      <div key={review.id || idx}>
                        {idx > 0 && <Separator className="mb-4" />}
                        <div className="space-y-2">
                          <div className="flex items-start justify-between">
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-foreground">
                                  {review.reviewer}
                                </span>
                                {review.verified && (
                                  <Badge
                                    variant="outline"
                                    className="border-primary/30 bg-primary/10 text-primary text-[10px]"
                                  >
                                    <CheckCircle className="mr-0.5 size-2.5" />
                                    已认证购买
                                  </Badge>
                                )}
                              </div>
                              <SmallStarRating rating={review.rating} />
                            </div>
                            <span className="text-xs text-muted-foreground">
                              {formatDate(review.date)}
                            </span>
                          </div>
                          {review.title && (
                            <p className="text-sm font-medium text-foreground">
                              {review.title}
                            </p>
                          )}
                          <p className="text-sm leading-relaxed text-muted-foreground">
                            {review.body}
                          </p>
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              )}
            </div>

            {/* 右列：库存、咨询 */}
            <div className="space-y-6">
              {/* 库存状态 */}
              <Card>
                <CardHeader>
                  <CardTitle>库存情况</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {product.in_stock ? (
                    <div className="flex items-center gap-2">
                      <CheckCircle className="size-5 text-emerald-500" />
                      <span className="font-medium text-emerald-700">
                        有货（{product.total_stock} 件）
                      </span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <XCircle className="size-5 text-red-500" />
                      <span className="font-medium text-red-700">
                        缺货
                      </span>
                    </div>
                  )}

                  {/* 各仓库存 */}
                  {product.warehouses &&
                    product.warehouses.length > 0 && (
                      <div className="space-y-2">
                        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                          各仓库存
                        </p>
                        {product.warehouses.map((ws) => (
                          <div
                            key={ws.name}
                            className="flex items-center justify-between rounded-lg bg-muted px-3 py-2"
                          >
                            <div className="flex items-center gap-2">
                              <MapPin className="size-3.5 text-muted-foreground" />
                              <span className="text-sm text-muted-foreground">
                                {ws.name}
                              </span>
                            </div>
                            <span
                              className={`text-sm font-medium ${
                                ws.quantity > 0
                                  ? "text-emerald-600"
                                  : "text-muted-foreground"
                              }`}
                            >
                              {ws.quantity}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                </CardContent>
              </Card>

              {/* 咨询按钮 */}
              <Button
                className="w-full bg-primary text-primary-foreground hover:opacity-90"
                onClick={() =>
                  router.push(
                    `/chat?q=${encodeURIComponent(
                      `请介绍一下${product.name}`
                    )}`
                  )
                }
              >
                <MessageSquare className="mr-2 size-4" />
                向智能体咨询该商品
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
