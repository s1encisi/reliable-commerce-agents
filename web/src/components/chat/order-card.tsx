"use client";

import Link from "next/link";
import {
  ExternalLink,
  Package,
  Truck,
  MapPin,
  CalendarDays,
  Clock,
  Navigation,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { OrderStatusBadge } from "@/components/status-badge";
import { formatPrice, formatDate } from "@/lib/format";

interface OrderItem {
  name?: string;
  quantity?: number;
  unit_price?: number;
  total?: number;
  category?: string;
  brand?: string;
}

interface TimelineEvent {
  label?: string;
  status?: string;
  date?: string;
}

interface ShippingAddressObj {
  street?: string;
  city?: string;
  state?: string;
  zip?: string;
  country?: string;
}

interface OrderData {
  id?: string;
  order_id?: string;
  status?: string;
  total?: number;
  date?: string;
  item_count?: number;
  items?: OrderItem[];
  tracking?: string;
  carrier?: string;
  shipping_address?: string | ShippingAddressObj;
  timeline?: TimelineEvent[];
  return_eligible?: boolean;
}

interface ChatOrderCardProps {
  data: OrderData;
  onAction?: (message: string) => void;
}

function formatAddress(addr: OrderData["shipping_address"]): string | null {
  if (!addr) return null;
  if (typeof addr === "string") return addr;
  const parts = [addr.street, addr.city, addr.state, addr.zip, addr.country].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}

const RETURN_WINDOW_DAYS = 30;

// 智能体可能已经调用过 check_return_eligibility 并显式设置了
// return_eligible。否则就退回到基于「已送达」时间线日期（再退一步用
// 下单日期）的客户端启发式判断——与
// shared/tools/return_tools.py 的 check_return_eligibility 采用同样的
// 30 天规则。当数据不足以推算出日期时，默认判定为可退货：把本可以正常
// 使用的「退货」按钮藏起来，比偶尔多显示一个智能体仍会拒绝的按钮更糟。
function isReturnEligible(data: OrderData): boolean {
  if (data.status !== "delivered") return false;
  if (data.return_eligible !== undefined) return data.return_eligible;

  const deliveredEvent = (data.timeline || []).find(
    (e) => (e.status || e.label || "").toLowerCase() === "delivered" && e.date
  );
  const referenceDate = deliveredEvent?.date || data.date;
  if (!referenceDate) return true;

  const parsed = new Date(referenceDate);
  if (Number.isNaN(parsed.getTime())) return true;

  const daysSince = (Date.now() - parsed.getTime()) / (1000 * 60 * 60 * 24);
  return daysSince <= RETURN_WINDOW_DAYS;
}

export function ChatOrderCard({ data, onAction }: ChatOrderCardProps) {
  const orderId = data.id || data.order_id || "";
  const shortId =
    orderId.length > 12
      ? `${orderId.slice(0, 8)}...${orderId.slice(-4)}`
      : orderId;
  const items = data.items || [];
  const timeline = data.timeline || [];
  const returnEligible = isReturnEligible(data);

  return (
    <div className="rounded-xl border border-border bg-card shadow-sm max-w-lg overflow-hidden">
      {/* 标题栏 */}
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Package className="size-4 text-muted-foreground" />
          <span className="font-mono text-xs font-medium text-muted-foreground">
            #{shortId}
          </span>
          {data.status && <OrderStatusBadge status={data.status} />}
        </div>
        <div className="flex items-center gap-1.5">
          {orderId && (
            <Link href={`/orders/${orderId}`} target="_blank" rel="noopener noreferrer">
              <Button size="sm" variant="outline" className="h-7 text-xs">
                <ExternalLink className="mr-1 size-3" /> 查看
              </Button>
            </Link>
          )}
          {(data.tracking || data.carrier) && onAction && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs border-info/30 text-info hover:bg-info/10"
              onClick={() => onAction(`帮我跟踪订单 #${data.id || data.order_id} 的物流`)}
            >
              <Navigation className="mr-1 size-3" />
              跟踪
            </Button>
          )}
          {(data.status === "placed" || data.status === "confirmed") && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs border-destructive/30 text-destructive hover:bg-destructive/10"
              onClick={() => onAction?.(`帮我取消订单 #${data.id || data.order_id}`)}
            >
              取消
            </Button>
          )}
          {returnEligible && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs border-warning/30 text-warning hover:bg-warning/10"
              onClick={() => onAction?.(`我要退货，订单 #${data.id || data.order_id}`)}
            >
              退货
            </Button>
          )}
        </div>
      </div>

      {/* 商品明细表 */}
      {items.length > 0 && (
        <div className="border-b border-border">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border">
                <th className="px-4 py-1.5 text-left font-medium text-muted-foreground">
                  商品
                </th>
                <th className="px-2 py-1.5 text-center font-medium text-muted-foreground w-12">
                  数量
                </th>
                <th className="px-4 py-1.5 text-right font-medium text-muted-foreground w-20">
                  金额
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((item, i) => (
                <tr key={i}>
                  <td className="px-4 py-2">
                    <div className="font-medium text-foreground leading-snug">
                      {item.name}
                    </div>
                    {(item.category || item.brand) && (
                      <div className="text-[10px] text-muted-foreground mt-0.5">
                        {[item.brand, item.category]
                          .filter(Boolean)
                          .join(" \u00b7 ")}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-2 text-center text-muted-foreground">
                    {item.quantity}
                  </td>
                  <td className="px-4 py-2 text-right text-foreground font-medium whitespace-nowrap">
                    {item.total != null || (item.unit_price != null && item.quantity != null)
                      ? formatPrice(item.total ?? item.unit_price! * item.quantity!)
                      : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 汇总信息 */}
      <div className="px-4 py-2.5 space-y-1.5">
        {/* 合计 + 日期 */}
        <div className="flex items-center justify-between">
          {data.total != null && (
            <span className="text-sm font-bold text-foreground">
              合计：{formatPrice(data.total)}
            </span>
          )}
          {data.date && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <CalendarDays className="size-3" />
              {formatDate(data.date)}
            </span>
          )}
        </div>

        {/* 物流信息 */}
        {(data.tracking || data.carrier) && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Truck className="size-3.5 shrink-0" />
            {data.carrier && <span>{data.carrier}</span>}
            {data.tracking && (
              <>
                {data.carrier && (
                  <span className="text-muted-foreground">&middot;</span>
                )}
                <span className="font-mono text-muted-foreground">
                  {data.tracking}
                </span>
              </>
            )}
          </div>
        )}

        {/* 收货地址 */}
        {formatAddress(data.shipping_address) && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <MapPin className="size-3.5 shrink-0" />
            <span className="line-clamp-1">{formatAddress(data.shipping_address)}</span>
          </div>
        )}

        {/* 时间线（精简版） */}
        {timeline.length > 0 && (
          <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground pt-0.5 flex-wrap">
            <Clock className="size-3 shrink-0" />
            {timeline.map((event, i) => (
              <span key={i} className="flex items-center gap-1">
                {i > 0 && <span className="text-muted-foreground">&rarr;</span>}
                <span className="text-muted-foreground">{event.status ?? event.label}</span>
                {event.date && <span>({formatDate(event.date)})</span>}
              </span>
            ))}
          </div>
        )}

        {/* 商品件数兜底（没有 items 数组时） */}
        {items.length === 0 && data.item_count != null && (
          <div className="text-xs text-muted-foreground">
            {data.item_count} 件商品
          </div>
        )}
      </div>
    </div>
  );
}
