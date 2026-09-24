"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api, type CartResponse, type Address } from "@/lib/api";
import { toastOrderPlaced } from "@/lib/toast";
import { formatPrice } from "@/lib/format";
import { productImageUrl } from "@/lib/images";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import {
  ArrowLeft,
  CreditCard,
  MapPin,
  Truck,
  CheckCircle,
  Loader2,
  Package,
  Tag,
} from "lucide-react";

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

interface AddressForm {
  name: string;
  street: string;
  city: string;
  state: string;
  zip: string;
  country: string;
  phone: string;
}

const EMPTY_ADDRESS: AddressForm = {
  name: "",
  street: "",
  city: "",
  state: "",
  zip: "",
  country: "中国",
  phone: "",
};

function addressFromApi(addr: Address | null): AddressForm {
  if (!addr) return { ...EMPTY_ADDRESS };
  return {
    name: addr.name ?? "",
    street: addr.street ?? "",
    city: addr.city ?? "",
    state: addr.state ?? "",
    zip: addr.zip ?? "",
    country: addr.country ?? "中国",
    phone: addr.phone ?? "",
  };
}

// ---------------------------------------------------------------------------
// 地址表单组件
// ---------------------------------------------------------------------------

function AddressFormFields({
  value,
  onChange,
  idPrefix,
}: {
  value: AddressForm;
  onChange: (addr: AddressForm) => void;
  idPrefix: string;
}) {
  function update(field: keyof AddressForm, val: string) {
    onChange({ ...value, [field]: val });
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="sm:col-span-2">
        <Label htmlFor={`${idPrefix}-name`}>收件人姓名</Label>
        <Input
          id={`${idPrefix}-name`}
          placeholder="张伟"
          value={value.name}
          onChange={(e) => update("name", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div className="sm:col-span-2">
        <Label htmlFor={`${idPrefix}-street`}>街道地址</Label>
        <Input
          id={`${idPrefix}-street`}
          placeholder="某某路 123 号 5 栋 601 室"
          value={value.street}
          onChange={(e) => update("street", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div>
        <Label htmlFor={`${idPrefix}-city`}>城市</Label>
        <Input
          id={`${idPrefix}-city`}
          placeholder="上海市"
          value={value.city}
          onChange={(e) => update("city", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div>
        <Label htmlFor={`${idPrefix}-state`}>省份</Label>
        <Input
          id={`${idPrefix}-state`}
          placeholder="上海市"
          value={value.state}
          onChange={(e) => update("state", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div>
        <Label htmlFor={`${idPrefix}-zip`}>邮政编码</Label>
        <Input
          id={`${idPrefix}-zip`}
          placeholder="200000"
          value={value.zip}
          onChange={(e) => update("zip", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div>
        <Label htmlFor={`${idPrefix}-country`}>国家/地区</Label>
        <Input
          id={`${idPrefix}-country`}
          placeholder="中国"
          value={value.country}
          onChange={(e) => update("country", e.target.value)}
          className="mt-1.5"
        />
      </div>
      <div className="sm:col-span-2">
        <Label htmlFor={`${idPrefix}-phone`}>
          手机号 <span className="text-muted-foreground">（选填）</span>
        </Label>
        <Input
          id={`${idPrefix}-phone`}
          placeholder="138 0000 0000"
          value={value.phone}
          onChange={(e) => update("phone", e.target.value)}
          className="mt-1.5"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export default function CheckoutPage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();
  const { refreshCart } = useCart();

  const [cart, setCart] = useState<CartResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 地址状态
  const [shipping, setShipping] = useState<AddressForm>({ ...EMPTY_ADDRESS });
  const [billing, setBilling] = useState<AddressForm>({ ...EMPTY_ADDRESS });
  const [billingSame, setBillingSame] = useState(true);

  // 结算状态
  const [placing, setPlacing] = useState(false);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);

  // 挂载时加载购物车
  const loadCart = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getCart();
      setCart(data);

      // 若购物车已存地址，则预填
      if (data.shipping_address) {
        setShipping(addressFromApi(data.shipping_address));
      }
      if (data.billing_address) {
        setBilling(addressFromApi(data.billing_address));
      }
      if (data.billing_same_as_shipping !== undefined) {
        setBillingSame(data.billing_same_as_shipping);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "购物车加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user) loadCart();
  }, [user, loadCart]);

  if (authLoading || !user) return null;

  // —— 校验 ——

  function validateShipping(): string | null {
    if (!shipping.street.trim()) return "请填写街道地址";
    if (!shipping.city.trim()) return "请填写城市";
    if (!shipping.state.trim()) return "请填写省份";
    if (!shipping.zip.trim()) return "请填写邮政编码";
    return null;
  }

  function validateBilling(): string | null {
    if (billingSame) return null;
    if (!billing.street.trim()) return "请填写账单街道地址";
    if (!billing.city.trim()) return "请填写账单城市";
    if (!billing.state.trim()) return "请填写账单省份";
    if (!billing.zip.trim()) return "请填写账单邮政编码";
    return null;
  }

  // —— 提交订单 ——

  async function handlePlaceOrder() {
    setCheckoutError(null);

    const shippingErr = validateShipping();
    if (shippingErr) {
      setCheckoutError(shippingErr);
      return;
    }
    const billingErr = validateBilling();
    if (billingErr) {
      setCheckoutError(billingErr);
      return;
    }

    setPlacing(true);
    try {
      const shippingAddr: Address = {
        name: shipping.name || undefined,
        street: shipping.street,
        city: shipping.city,
        state: shipping.state,
        zip: shipping.zip,
        country: shipping.country || "中国",
        phone: shipping.phone || undefined,
      };

      const billingAddr: Address | null = billingSame
        ? null
        : {
            name: billing.name || undefined,
            street: billing.street,
            city: billing.city,
            state: billing.state,
            zip: billing.zip,
            country: billing.country || "中国",
            phone: billing.phone || undefined,
          };

      const result = await api.checkout({
        shipping_address: shippingAddr,
        billing_address: billingAddr,
        billing_same_as_shipping: billingSame,
      });

      // 刷新购物车上下文（此时购物车已清空）
      await refreshCart();
      toastOrderPlaced(result.order_id);
      router.push(`/orders/${result.order_id}?placed=true`);
    } catch (err) {
      setCheckoutError(
        err instanceof Error ? err.message : "结算失败，请稍后重试。"
      );
    } finally {
      setPlacing(false);
    }
  }

  // —— 加载状态 ——

  if (loading) {
    return (
      <div className="min-h-screen bg-background">
        <div className="border-b border-border bg-card">
          <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6 lg:px-8">
            <Button
              variant="ghost"
              size="sm"
              className="-ml-2 text-muted-foreground hover:text-muted-foreground"
              onClick={() => router.push("/cart")}
            >
              <ArrowLeft className="mr-1.5 size-4" />
              返回购物车
            </Button>
          </div>
        </div>
        <div className="flex items-center justify-center py-32">
          <Loader2 className="size-8 animate-spin text-muted-foreground" />
        </div>
      </div>
    );
  }

  // —— 空购物车 ——

  if (!cart || cart.items.length === 0) {
    return (
      <div className="min-h-screen bg-background">
        <div className="border-b border-border bg-card">
          <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6 lg:px-8">
            <Button
              variant="ghost"
              size="sm"
              className="-ml-2 text-muted-foreground hover:text-muted-foreground"
              onClick={() => router.push("/cart")}
            >
              <ArrowLeft className="mr-1.5 size-4" />
              返回购物车
            </Button>
          </div>
        </div>
        <div className="mx-auto max-w-4xl px-4 py-20 text-center sm:px-6 lg:px-8">
          <Package className="mx-auto size-12 text-muted-foreground" />
          <h2 className="mt-4 text-lg font-semibold text-muted-foreground">
            购物车是空的
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            请先添加商品再进行结算。
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

  // —— 购物车加载失败 ——

  if (error) {
    return (
      <div className="min-h-screen bg-background">
        <div className="border-b border-border bg-card">
          <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6 lg:px-8">
            <Button
              variant="ghost"
              size="sm"
              className="-ml-2 text-muted-foreground hover:text-muted-foreground"
              onClick={() => router.push("/cart")}
            >
              <ArrowLeft className="mr-1.5 size-4" />
              返回购物车
            </Button>
          </div>
        </div>
        <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      {/* 页头 */}
      <div className="border-b border-border bg-card">
        <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6 lg:px-8">
          <Button
            variant="ghost"
            size="sm"
            className="-ml-2 text-muted-foreground hover:text-muted-foreground"
            onClick={() => router.push("/cart")}
          >
            <ArrowLeft className="mr-1.5 size-4" />
            返回购物车
          </Button>
          <div className="mt-4 flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
              <CheckCircle className="size-5 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-foreground">结算</h1>
              <p className="text-sm text-muted-foreground">
                完成您的订单（{cart.item_count} 件商品）
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* 内容区 */}
      <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="space-y-6">
          {/* 第 1 部分：收货地址 */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <MapPin className="size-4 text-muted-foreground" />
                收货地址
              </CardTitle>
            </CardHeader>
            <CardContent>
              <AddressFormFields
                value={shipping}
                onChange={setShipping}
                idPrefix="ship"
              />
            </CardContent>
          </Card>

          {/* 第 2 部分：账单地址 */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CreditCard className="size-4 text-muted-foreground" />
                账单地址
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={billingSame}
                  onChange={(e) => setBillingSame(e.target.checked)}
                  className="size-4 rounded border-border text-primary focus:ring-primary"
                />
                <span className="text-sm text-muted-foreground">
                  与收货地址相同
                </span>
              </label>
              {!billingSame && (
                <AddressFormFields
                  value={billing}
                  onChange={setBilling}
                  idPrefix="bill"
                />
              )}
            </CardContent>
          </Card>

          {/* 第 3 部分：订单确认 */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Package className="size-4 text-muted-foreground" />
                订单确认
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* 商品清单 */}
              <div className="divide-y divide-border">
                {cart.items.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-center gap-3 py-3 first:pt-0 last:pb-0"
                  >
                    <img
                      src={productImageUrl(item.product_id, 48, 48, item.image_url, item.category)}
                      alt={item.name}
                      className="size-12 shrink-0 rounded-md object-cover bg-muted"
                      loading="lazy"
                    />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-foreground truncate">
                        {item.name}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {item.quantity} × {formatPrice(item.price)}
                      </p>
                    </div>
                    <span className="text-sm font-medium text-foreground shrink-0">
                      {formatPrice(item.subtotal)}
                    </span>
                  </div>
                ))}
              </div>

              <Separator />

              {/* 金额汇总 */}
              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">小计</span>
                  <span className="text-muted-foreground">
                    {formatPrice(cart.subtotal)}
                  </span>
                </div>
                {cart.coupon_code && cart.discount_amount > 0 && (
                  <div className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-1.5 text-muted-foreground">
                      <Tag className="size-3 text-emerald-500" />
                      优惠券（{cart.coupon_code}）
                    </span>
                    <span className="text-emerald-600">
                      -{formatPrice(cart.discount_amount)}
                    </span>
                  </div>
                )}
                <Separator />
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-muted-foreground">
                    合计
                  </span>
                  <span className="text-xl font-bold text-foreground">
                    {formatPrice(cart.total)}
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 第 4 部分：支付方式 */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CreditCard className="size-4 text-muted-foreground" />
                支付方式
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="rounded-xl border border-border bg-gradient-to-br from-slate-800 to-slate-900 p-6 text-white">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium uppercase tracking-wider text-slate-400">
                    演示支付
                  </span>
                  <CreditCard className="size-6 text-slate-400" />
                </div>
                <div className="mt-6 font-mono text-lg tracking-widest">
                  6222 0202 0000 0000
                </div>
                <div className="mt-4 flex items-center justify-between text-xs text-slate-400">
                  <span>演示卡</span>
                  <span>12/99</span>
                </div>
              </div>
              <p className="mt-3 text-center text-xs text-muted-foreground">
                这是演示应用，不会产生真实支付。
              </p>
            </CardContent>
          </Card>

          {/* 错误提示 */}
          {checkoutError && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {checkoutError}
            </div>
          )}

          {/* 提交订单按钮 */}
          <Button
            className="w-full bg-primary hover:opacity-90"
            size="lg"
            disabled={placing}
            onClick={handlePlaceOrder}
          >
            {placing ? (
              <>
                <Loader2 className="mr-2 size-4 animate-spin" />
                正在提交订单…
              </>
            ) : (
              <>
                <Truck className="mr-2 size-4" />
                提交订单 —— {formatPrice(cart.total)}
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
