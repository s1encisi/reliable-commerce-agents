"use client";

import Link from "next/link";
import { Download, RotateCcw, Package, Clock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { apiUrl } from "@/lib/api";

interface ReturnData {
  order_id?: string;
  return_id?: string;
  status?: string;
  return_label_url?: string;
  refund_amount?: number;
  refund_method?: string;
  refund_timeline?: string;
}

export function ChatReturnCard({ data }: { data: ReturnData }) {
  return (
    <div className="my-2 max-w-md rounded-xl border border-warning/30 bg-warning/5 p-5 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <RotateCcw className="size-4 text-warning" />
          <span className="font-semibold text-foreground">已发起退货</span>
        </div>
        {data.status && (
          <Badge variant="outline" className="border-warning/30 bg-warning/10 text-warning text-xs">
            {data.status}
          </Badge>
        )}
      </div>

      {data.return_label_url && (
        <a
          href={apiUrl(data.return_label_url)}
          target="_blank"
          rel="noopener noreferrer"
        >
          <Button variant="outline" className="w-full gap-2 border-warning/40 text-warning hover:bg-warning/10">
            <Download className="size-4" />
            下载退货面单
          </Button>
        </a>
      )}

      <p className="text-xs text-muted-foreground flex items-center gap-1.5">
        <Package className="size-3.5" />
        打印面单，把商品打包好，然后到任意承运商网点寄回
      </p>

      <Separator className="bg-warning/20" />

      <div className="space-y-1.5 text-sm">
        {data.refund_amount != null && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">退款金额</span>
            <span className="font-medium text-success">¥{data.refund_amount.toFixed(2)}</span>
          </div>
        )}
        {data.refund_method && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">退款方式</span>
            <span className="text-foreground">{data.refund_method.replace(/_/g, " ")}</span>
          </div>
        )}
        {data.refund_timeline && (
          <div className="flex justify-between">
            <span className="text-muted-foreground">到账时间</span>
            <span className="flex items-center gap-1 text-foreground">
              <Clock className="size-3" />
              {data.refund_timeline}
            </span>
          </div>
        )}
      </div>

      {data.order_id && (
        <Link href={`/orders/${data.order_id}`} className="block text-center text-xs text-primary hover:underline mt-2">
          查看订单详情
        </Link>
      )}
    </div>
  );
}
