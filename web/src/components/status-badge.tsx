import { StatusBadge, type SemanticTone } from "@/components/ui/status-badge";
import { type LucideIcon, Package, Truck, CheckCircle, XCircle, RotateCcw, Clock, ShoppingCart } from "lucide-react";

// 订单域状态 -> 通用 StatusBadge 原语（Phase 8.4 阶段 3）——本文件现在只负责
// 订单特有的「标签 / 图标 / 语义色调」映射，色调到颜色的换算逻辑只存在于
// ui/status-badge.tsx 一处。
// 注意：键名（placed / shipped / ...）是后端返回的机器值，必须保持英文。
const STATUS_CONFIG: Record<string, { label: string; tone: SemanticTone; icon: LucideIcon }> = {
  placed: { label: "已下单", tone: "info", icon: ShoppingCart },
  confirmed: { label: "已确认", tone: "info", icon: Package },
  shipped: { label: "已发货", tone: "warning", icon: Truck },
  out_for_delivery: { label: "配送中", tone: "warning", icon: Truck },
  delivered: { label: "已送达", tone: "success", icon: CheckCircle },
  returned: { label: "已退货", tone: "warning", icon: RotateCcw },
  cancelled: { label: "已取消", tone: "destructive", icon: XCircle },
};

/** 按订单状态渲染徽章；遇到未知状态时原样回显状态值。 */
export function OrderStatusBadge({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status] || { label: status, tone: "neutral" as const, icon: Clock };
  return <StatusBadge label={cfg.label} tone={cfg.tone} icon={cfg.icon} />;
}

export { STATUS_CONFIG };
