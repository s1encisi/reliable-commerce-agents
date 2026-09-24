import { type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * 生成式 UI 中任何状态 / 健康度信号都会归入这五种色调——完全由
 * success/warning/info/destructive 设计令牌驱动（Phase 8.4 阶段 1），
 * 从不使用字面颜色值。`neutral` 是所有非正负向、非警示、非信息类信号的
 * 兜底选项。
 */
export type SemanticTone = "success" | "warning" | "info" | "destructive" | "neutral";

/** 按色调索引的徽章背景 / 文字 / 边框类名。 */
export const TONE_BADGE_CLASS: Record<SemanticTone, string> = {
  success: "bg-success/10 text-success border-success/30 dark:bg-success/15",
  warning: "bg-warning/10 text-warning border-warning/30 dark:bg-warning/15",
  info: "bg-info/10 text-info border-info/30 dark:bg-info/15",
  destructive: "bg-destructive/10 text-destructive border-destructive/30 dark:bg-destructive/15",
  neutral: "bg-muted/50 text-muted-foreground border-border",
};

/** 按色调索引的纯文字色类——用于没有徽章外壳的图标 / 数值。 */
export const TONE_TEXT_CLASS: Record<SemanticTone, string> = {
  success: "text-success",
  warning: "text-warning",
  info: "text-info",
  destructive: "text-destructive",
  neutral: "text-muted-foreground",
};

export interface StatusBadgeProps {
  label: string;
  tone: SemanticTone;
  icon?: LucideIcon;
  className?: string;
}

/**
 * 通用状态 / 健康度徽章，适用于专业智能体产出的任何布尔值或枚举值
 * （in_stock、risk_level、sentiment……）——由色调查表驱动，而非按领域硬编码
 * 颜色分支，因此所有领域特有的用法（订单状态、库存水平、情感风险）共用
 * 同一套映射。
 */
export function StatusBadge({ label, tone, icon: Icon, className }: StatusBadgeProps) {
  return (
    <Badge variant="outline" className={cn(TONE_BADGE_CLASS[tone], className)}>
      {Icon && <Icon className="mr-1 size-3" />}
      {label}
    </Badge>
  );
}
