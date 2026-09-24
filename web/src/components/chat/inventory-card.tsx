"use client";

import { Truck, Warehouse } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { StatusBadge } from "@/components/ui/status-badge";
import { StatTile } from "@/components/ui/stat-tile";
import { formatPrice } from "@/lib/format";

interface WarehouseStock {
  warehouse?: string;
  region?: string;
  quantity?: number;
  low_stock?: boolean;
  [key: string]: unknown;
}

interface RestockEntry {
  warehouse?: string;
  region?: string;
  expected_quantity?: number;
  expected_date?: string;
  [key: string]: unknown;
}

interface ShipsFrom {
  warehouse?: string;
  region?: string;
  quantity_available?: number;
}

interface ShippingOption {
  carrier: string;
  speed_tier?: string;
  price: number;
  delivery_window?: string;
}

interface InventoryData {
  product_id?: string;
  product_name?: string;
  in_stock?: boolean;
  total_quantity?: number;
  warehouses?: WarehouseStock[];
  upcoming_restocks?: RestockEntry[];
  next_restock?: string;
  ships_from?: ShipsFrom;
  shipping_options?: ShippingOption[];
}

const WAREHOUSE_COLUMNS: DataTableColumn<WarehouseStock>[] = [
  { key: "warehouse", header: "仓库" },
  { key: "region", header: "区域" },
  {
    key: "quantity",
    header: "数量",
    align: "right",
    render: (r) => (
      <span className={r.low_stock ? "text-warning font-medium" : undefined}>
        {r.quantity ?? "—"}
        {r.low_stock ? "（库存偏低）" : ""}
      </span>
    ),
  },
];

const RESTOCK_COLUMNS: DataTableColumn<RestockEntry>[] = [
  { key: "warehouse", header: "仓库" },
  { key: "expected_quantity", header: "数量", align: "right" },
  { key: "expected_date", header: "预计到货" },
];

interface ChatInventoryCardProps {
  data: InventoryData;
  /** 重新向对话发起提问，例如在选定配送方式之后——「选择承运商」没有像购物车
   * 添加商品那样的直接写入接口，因此这里沿用已有的跟踪/取消/退货再提问模式
   * （order-card.tsx），而不是另造第三套机制。 */
  onAction?: (message: string) => void;
}

export function ChatInventoryCard({ data, onAction }: ChatInventoryCardProps) {
  // 没有任何内容可展示——例如工具调用没取到数据，但模型仍然输出了一段
  // 全空的代码块。此时不要渲染一个下方空白的标题栏。仅有 product_name
  // 也算有效：它至少说明了这是哪件商品。
  const hasAnyData =
    data.product_name != null ||
    data.in_stock != null ||
    data.total_quantity != null ||
    (data.warehouses && data.warehouses.length > 0) ||
    (data.upcoming_restocks && data.upcoming_restocks.length > 0) ||
    data.next_restock != null ||
    (data.shipping_options && data.shipping_options.length > 0);
  if (!hasAnyData) return null;

  return (
    <div className="my-2 max-w-md rounded-xl border border-border bg-card shadow-sm overflow-hidden">
      {/* 标题栏 */}
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted px-4 py-2.5">
        <div className="flex items-center gap-2 min-w-0">
          <Warehouse className="size-4 text-muted-foreground shrink-0" />
          <span className="text-sm font-medium text-foreground truncate">
            {data.product_name || "库存与履约"}
          </span>
        </div>
        {data.in_stock != null && (
          <StatusBadge
            label={data.in_stock ? "有货" : "缺货"}
            tone={data.in_stock ? "success" : "destructive"}
          />
        )}
      </div>

      <div className="p-4 space-y-3">
        {data.total_quantity != null && (
          <StatTile
            label="库存总量"
            value={data.total_quantity}
            tone={data.total_quantity > 0 ? "success" : "destructive"}
          />
        )}

        {data.warehouses && data.warehouses.length > 0 && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">按仓库</p>
            <DataTable columns={WAREHOUSE_COLUMNS} rows={data.warehouses} />
          </div>
        )}

        {data.upcoming_restocks && data.upcoming_restocks.length > 0 && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">即将补货</p>
            <DataTable columns={RESTOCK_COLUMNS} rows={data.upcoming_restocks} />
          </div>
        )}

        {data.next_restock && (!data.upcoming_restocks || data.upcoming_restocks.length === 0) && (
          <p className="text-[11px] text-muted-foreground">下次补货：{data.next_restock}</p>
        )}

        {data.shipping_options && data.shipping_options.length > 0 && (
          <div>
            <p className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground mb-1">
              <Truck className="size-3" />
              配送方式
              {data.ships_from?.warehouse && ` —— 发货仓库：${data.ships_from.warehouse}`}
            </p>
            <div className="space-y-1.5">
              {data.shipping_options.map((opt, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-xs"
                >
                  <div className="min-w-0">
                    <div className="font-medium text-foreground truncate">
                      {opt.carrier}
                      {opt.speed_tier && <span className="text-muted-foreground"> · {opt.speed_tier}</span>}
                    </div>
                    {opt.delivery_window && (
                      <div className="text-[10px] text-muted-foreground">{opt.delivery_window}</div>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="font-semibold text-foreground">{formatPrice(opt.price)}</span>
                    {onAction && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-6 text-[11px] px-2"
                        onClick={() =>
                          onAction(
                            `我选择${opt.carrier}${opt.speed_tier ? `（${opt.speed_tier}）` : ""}配送，运费 ${formatPrice(opt.price)}${opt.delivery_window ? `，${opt.delivery_window}` : ""}。`
                          )
                        }
                      >
                        选择
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
