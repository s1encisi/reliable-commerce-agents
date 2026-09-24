"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Package,
  Plus,
  Star,
  Loader2,
  CheckCircle,
  XCircle,
} from "lucide-react";
import { productImageUrl } from "@/lib/images";
import { formatPrice } from "@/lib/format";

export default function SellerProductsPage() {
  const router = useRouter();
  const { user, isLoading: authLoading, isAdmin } = useAuth();

  const [products, setProducts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isSeller = user?.role === "seller" || isAdmin;

  const loadProducts = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getSellerProducts();
      setProducts(data.products);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "商品加载失败",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user && isSeller) loadProducts();
  }, [user, isSeller, loadProducts]);

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
            商品管理仅对商家和管理员开放。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      {/* 页头 */}
      <div className="border-b border-border bg-card">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
                <Package className="size-5 text-primary-foreground" />
              </div>
              <div>
                <h1 className="text-2xl font-bold text-foreground">
                  我的商品
                </h1>
                <p className="text-sm text-muted-foreground">
                  商品目录中共 {products.length} 件商品
                </p>
              </div>
            </div>

            <Dialog>
              <DialogTrigger
                render={
                  <Button className="bg-primary text-primary-foreground hover:opacity-90" />
                }
              >
                <Plus className="mr-2 size-4" />
                添加商品
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>即将上线</DialogTitle>
                  <DialogDescription>
                    商品创建功能尚未开放。完整的商家能力（商品创建、编辑与库存管理）
                    已在规划中。
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter showCloseButton>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>
        </div>
      </div>

      {/* 内容区 */}
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

        {/* 空状态 */}
        {!loading && !error && products.length === 0 && (
          <div className="py-20 text-center">
            <Package className="mx-auto size-10 text-muted-foreground" />
            <p className="mt-3 text-sm text-muted-foreground">
              您还没有商品。
            </p>
          </div>
        )}

        {/* 商品表格 */}
        {!loading && !error && products.length > 0 && (
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[60px]">图片</TableHead>
                    <TableHead>商品</TableHead>
                    <TableHead>品类</TableHead>
                    <TableHead className="text-right">价格</TableHead>
                    <TableHead className="text-right">评分</TableHead>
                    <TableHead className="text-center">状态</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {products.map((product: any) => (
                    <TableRow
                      key={product.id}
                      className="cursor-pointer"
                      onClick={() =>
                        router.push(`/products/${product.id}`)
                      }
                    >
                      <TableCell>
                        <img
                          src={productImageUrl(product.id, 48, 48, product.image_url, product.category)}
                          alt={product.name}
                          className="size-10 rounded-md object-cover bg-muted"
                        />
                      </TableCell>
                      <TableCell>
                        <div>
                          <p className="font-medium text-foreground">
                            {product.name}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {product.brand}
                          </p>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-[10px]">
                          {product.category}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <div>
                          <span className="font-medium text-foreground">
                            {formatPrice(product.price)}
                          </span>
                          {product.original_price &&
                            product.original_price > product.price && (
                              <span className="ml-1 text-xs text-muted-foreground line-through">
                                {formatPrice(product.original_price)}
                              </span>
                            )}
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        <span className="flex items-center justify-end gap-1 text-xs">
                          <Star className="size-3 fill-amber-400 text-amber-400" />
                          {product.rating?.toFixed(1) ?? "暂无"}
                          <span className="text-muted-foreground">
                            （{product.review_count ?? 0}）
                          </span>
                        </span>
                      </TableCell>
                      <TableCell className="text-center">
                        {product.is_active ? (
                          <span className="inline-flex items-center gap-1 text-xs text-green-700">
                            <CheckCircle className="size-3" />
                            已上架
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs text-red-600">
                            <XCircle className="size-3" />
                            已下架
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
