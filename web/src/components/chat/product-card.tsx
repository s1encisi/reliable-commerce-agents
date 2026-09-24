"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Star, ExternalLink, ShoppingCart, ShoppingBag, Check, AlertCircle, GitCompare, MessageSquare, LogIn } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { productImageUrl } from "@/lib/images";
import { useCart } from "@/lib/cart-context";
import { useAuth } from "@/lib/auth-context";

interface ProductData {
  id?: string;
  name?: string;
  price?: number;
  original_price?: number;
  image_url?: string;
  rating?: number;
  review_count?: number;
  category?: string;
  brand?: string;
  description?: string;
  on_sale?: boolean;
}

// 复用应用已有的分类图表配色（chart-1..5，本身已是 OKLCH 设计令牌），
// 而不是另写一次性的 Tailwind 颜色字面量——分类标签正是这些令牌存在的
// 场景：「区分 N 个分类取值」。`books` 改用第一阶段的 `warning` 令牌
// （色相接近琥珀色，且没有第 5 个图表槽位可用）；`sports` 与其原先的
// 橙色字面量有所偏离，因为没有任何图表槽位是橙色系——此处如实说明，
// 并非等价替换。
const CATEGORY_COLORS: Record<string, string> = {
  electronics: "bg-chart-1/10 text-chart-1 border-chart-1/30 dark:bg-chart-1/15",
  clothing: "bg-chart-3/10 text-chart-3 border-chart-3/30 dark:bg-chart-3/15",
  home: "bg-chart-4/10 text-chart-4 border-chart-4/30 dark:bg-chart-4/15",
  sports: "bg-chart-2/10 text-chart-2 border-chart-2/30 dark:bg-chart-2/15",
  books: "bg-warning/10 text-warning border-warning/30 dark:bg-warning/15",
};

interface ChatProductCardProps {
  data: ProductData;
  onAction?: (message: string) => void;
}

/**
 * 商品/订单 id 是 Postgres UUID。负责生成卡片的 LLM 已被要求从工具返回
 * 结果中原样复制真实 id，但它偶尔会自己编一个由名称派生的 slug
 * （如 "sony-wh1000xm5-001"）。这类 id 请求 /api/cart/items 与
 * /products/[id] 都会 404，表现为「加入购物车」按钮只会变成「重试」。
 * 因此把任何不是 UUID 的值一律视为没有 id：卡片照常渲染（仍可对比 /
 * 查看评论，这两项靠名称工作），只是不再提供注定失败的操作按钮。
 */
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function ChatProductCard({ data, onAction }: ChatProductCardProps) {
  const { cart, addItem } = useCart();
  const { isAuthenticated } = useAuth();
  const router = useRouter();
  const [optimisticAdded, setOptimisticAdded] = useState(false);
  const [error, setError] = useState(false);

  if (!data.name) return null;

  const productId = data.id && UUID_RE.test(data.id) ? data.id : undefined;

  // 「已加入」由购物车状态推导而来，因此能跨重新渲染保持，并反映真实
  // 情况（例如从别的页面返回该会话之后）。
  const inCart =
    !!productId && !!cart?.items?.some((i) => i.product_id === productId);
  const showAdded = inCart || optimisticAdded;

  const hasDiscount =
    data.original_price && data.original_price > (data.price || 0);
  const discountPct = hasDiscount
    ? Math.round((1 - (data.price || 0) / data.original_price!) * 100)
    : 0;
  const catColor =
    CATEGORY_COLORS[(data.category || "").toLowerCase()] ||
    "bg-muted text-muted-foreground border-border";

  return (
    <div className="rounded-xl border border-border bg-card shadow-sm max-w-md overflow-hidden transition-shadow hover:shadow-md">
      {/* 上部：图片 + 信息 */}
      <div className="flex gap-3 p-3 pb-2">
        {/* 图片 */}
        <div className="size-20 shrink-0 rounded-lg overflow-hidden bg-muted flex items-center justify-center">
          {productId ? (
            <img
              src={productImageUrl(productId, 100, 100, data.image_url, data.category)}
              alt={data.name}
              className="size-full object-cover"
              loading="lazy"
            />
          ) : (
            <ShoppingBag className="size-7 text-muted-foreground" />
          )}
        </div>

        {/* 信息 */}
        <div className="flex flex-1 flex-col gap-0.5 min-w-0">
          <h4 className="text-sm font-semibold text-foreground line-clamp-2 leading-tight">
            {data.name}
          </h4>

          <div className="flex items-center gap-1.5 flex-wrap">
            {data.brand && (
              <span className="text-[11px] text-muted-foreground">{data.brand}</span>
            )}
            {data.category && (
              <Badge
                variant="outline"
                className={`text-[10px] px-1.5 py-0 ${catColor}`}
              >
                {data.category}
              </Badge>
            )}
          </div>

          {data.description && (
            <p className="text-[11px] text-muted-foreground line-clamp-2 leading-snug">
              {data.description}
            </p>
          )}

          {/* 价格 */}
          <div className="flex items-center gap-1.5 mt-auto pt-0.5">
            {data.price != null && (
              <span className="text-base font-bold text-primary">
                ¥{data.price.toFixed(2)}
              </span>
            )}
            {hasDiscount && (
              <span className="text-xs text-muted-foreground line-through">
                ¥{data.original_price!.toFixed(2)}
              </span>
            )}
            {hasDiscount && discountPct > 0 && (
              <Badge className="bg-destructive text-white border-0 text-[9px] px-1.5 py-0">
                省 {discountPct}%
              </Badge>
            )}
          </div>

          {/* 评分 */}
          {data.rating != null && (
            <div className="flex items-center gap-1">
              <div className="flex items-center">
                {Array.from({ length: 5 }, (_, i) => (
                  <Star
                    key={i}
                    className={`size-3 ${
                      i < Math.round(data.rating!)
                        ? "fill-warning text-warning"
                        : "fill-muted text-muted"
                    }`}
                  />
                ))}
              </div>
              <span className="text-[11px] text-muted-foreground">
                {data.rating.toFixed(1)}
                {data.review_count != null && ` (${data.review_count})`}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* 操作按钮 */}
      {(productId || onAction) && (
        <div className="flex items-center gap-2 border-t border-border px-3 py-2">
          {productId && !isAuthenticated && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={(e) => {
                e.stopPropagation();
                router.push("/login");
              }}
            >
              <LogIn className="mr-1 size-3" />
              登录后购买
            </Button>
          )}
          {productId && isAuthenticated && (
            <Button
              size="sm"
              className={`h-7 text-xs ${
                error
                  ? "bg-destructive hover:bg-destructive/90 text-white"
                  : showAdded
                    ? "bg-success hover:bg-success/90 text-success-foreground"
                    : "bg-primary hover:opacity-90 text-primary-foreground"
              }`}
              disabled={showAdded && !error}
              onClick={(e) => {
                e.stopPropagation();
                if (!productId) return;
                // 乐观更新——先立即切换 UI，网络请求放到后台，
                // 保证对话界面保持响应。
                setError(false);
                setOptimisticAdded(true);
                addItem(productId)
                  .then(() => {
                    // 与手动输入消息的流程保持一致：让对话展示更新后的
                    // 购物车，从而渲染出结算卡片。
                    if (onAction && data.name) {
                      onAction(`我刚把 ${data.name} 加入购物车，给我看看更新后的购物车。`);
                    }
                  })
                  .catch(() => {
                    setOptimisticAdded(false);
                    setError(true);
                    setTimeout(() => setError(false), 2500);
                  });
              }}
            >
              {error ? (
                <AlertCircle className="mr-1 size-3" />
              ) : showAdded ? (
                <Check className="mr-1 size-3" />
              ) : (
                <ShoppingCart className="mr-1 size-3" />
              )}
              {error ? "重试" : showAdded ? "已加入" : "加入购物车"}
            </Button>
          )}
          {productId && (
            <Link
              href={isAuthenticated ? `/products/${productId}` : `/shop/products/${productId}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              <Button size="sm" variant="outline" className="h-7 text-xs">
                <ExternalLink className="mr-1 size-3" />
                详情
              </Button>
            </Link>
          )}
          {onAction && data.name && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={(e) => {
                e.stopPropagation();
                onAction(`把 ${data.name} 和同类商品对比一下`);
              }}
            >
              <GitCompare className="mr-1 size-3" />
              对比
            </Button>
          )}
          {onAction && data.name && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={(e) => {
                e.stopPropagation();
                onAction(`${data.name} 的评论怎么样？`);
              }}
            >
              <MessageSquare className="mr-1 size-3" />
              查看评论
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
