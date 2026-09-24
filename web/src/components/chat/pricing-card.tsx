"use client";

import { Tag, Ticket } from "lucide-react";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { formatPrice } from "@/lib/format";

interface SavingsLine {
  type: "coupon" | "bundle_promotion" | "buy_x_get_y" | "flash_sale" | "loyalty_discount";
  code?: string;
  name?: string;
  description?: string;
  tier?: string;
  product?: string;
  amount: number;
}

interface DealCoupon {
  code: string;
  description?: string;
  discount_type?: "percentage" | "fixed";
  discount_value?: number;
  min_spend?: number;
  valid_until?: string;
  [key: string]: unknown;
}

interface DealPromotion {
  name: string;
  type?: string;
  end_date?: string;
  [key: string]: unknown;
}

interface PricingData {
  original_total?: number;
  savings?: SavingsLine[];
  total_savings?: number;
  final_total?: number;
  savings_percentage?: number;
  coupons?: DealCoupon[];
  promotions?: DealPromotion[];
}

function savingsLabel(line: SavingsLine): string {
  switch (line.type) {
    case "coupon":
      return line.code ? `优惠券 ${line.code}` : "优惠券";
    case "loyalty_discount":
      return line.tier ? `${line.tier} 会员折扣` : "会员折扣";
    case "bundle_promotion":
      return line.name || "组合优惠";
    case "buy_x_get_y":
      return line.name ? `${line.name}${line.product ? `（${line.product}）` : ""}` : "买赠活动";
    case "flash_sale":
      return line.name ? `${line.name}${line.product ? `（${line.product}）` : ""}` : "限时秒杀";
    default:
      return line.description || "优惠";
  }
}

const COUPON_COLUMNS: DataTableColumn<DealCoupon>[] = [
  { key: "code", header: "优惠码" },
  {
    key: "description",
    header: "说明",
    // 过长的说明会把「折扣」列挤出卡片的 max-w-md 宽度——表格单元格默认
    // 是 whitespace-nowrap（ui/table.tsx），没有宽度上限时整行只会被撑开
    // 而不会换行。这是实测发现的问题：WELCOME10 的完整说明把它自己的
    // 折扣百分比遮住了。
    render: (r) => (
      <span className="block max-w-[140px] truncate" title={r.description}>
        {r.description ?? "—"}
      </span>
    ),
  },
  {
    key: "discount_value",
    header: "折扣",
    align: "right",
    render: (r) =>
      r.discount_value == null
        ? "—"
        : r.discount_type === "fixed"
          ? formatPrice(r.discount_value)
          : `${r.discount_value}%`,
  },
];

const PROMOTION_COLUMNS: DataTableColumn<DealPromotion>[] = [
  { key: "name", header: "促销活动" },
  { key: "type", header: "类型" },
  { key: "end_date", header: "结束时间" },
];

export function ChatPricingCard({ data }: { data: PricingData }) {
  const hasWaterfall = data.original_total != null && data.savings && data.savings.length > 0;
  const hasCoupons = data.coupons && data.coupons.length > 0;
  const hasPromotions = data.promotions && data.promotions.length > 0;

  // 没有任何内容可展示——例如 optimize_cart 没能取到购物车，但模型仍然
  // 输出了一段全空的代码块。此时不要渲染一个下方空白的标题栏。
  if (!hasWaterfall && !hasCoupons && !hasPromotions) return null;

  return (
    <div className="my-2 max-w-md rounded-xl border border-border bg-card shadow-sm overflow-hidden">
      {/* 标题栏 */}
      <div className="flex items-center gap-2 border-b border-border bg-muted px-4 py-2.5">
        <Tag className="size-4 text-muted-foreground shrink-0" />
        <span className="text-sm font-medium text-foreground">
          {hasWaterfall ? "优惠明细" : "优惠与促销"}
        </span>
      </div>

      <div className="p-4 space-y-3">
        {/* 优惠瀑布 */}
        {hasWaterfall && (
          <div className="text-sm space-y-1.5">
            <div className="flex items-center justify-between text-muted-foreground">
              <span>原价合计</span>
              <span>{formatPrice(data.original_total!)}</span>
            </div>
            {data.savings!.map((line, i) => (
              <div key={i} className="flex items-center justify-between text-success">
                <span>{savingsLabel(line)}</span>
                <span>-{formatPrice(line.amount)}</span>
              </div>
            ))}
            <div className="border-t border-border pt-1.5 flex items-center justify-between font-semibold text-foreground">
              <span>
                实付合计
                {data.savings_percentage != null && (
                  <span className="ml-1 text-xs font-normal text-success">
                    （已省 {data.savings_percentage}%）
                  </span>
                )}
              </span>
              <span>{formatPrice(data.final_total ?? data.original_total! - (data.total_savings ?? 0))}</span>
            </div>
          </div>
        )}

        {/* 可用优惠券 */}
        {hasCoupons && (
          <div>
            <p className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground mb-1">
              <Ticket className="size-3" /> 可用优惠券
            </p>
            <DataTable columns={COUPON_COLUMNS} rows={data.coupons!} />
          </div>
        )}

        {/* 进行中的促销 */}
        {hasPromotions && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">进行中的促销</p>
            <DataTable columns={PROMOTION_COLUMNS} rows={data.promotions!} />
          </div>
        )}
      </div>
    </div>
  );
}
