import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export interface StatDelta {
  /** 已预先格式化好的变化量文案，例如「本周 +3」或「-12%」。 */
  value: string;
  trend: "up" | "down" | "neutral";
}

export interface StatCardProps {
  label: string;
  value: string | number;
  icon?: LucideIcon;
  delta?: StatDelta;
  /** 数值下方的次要说明行。 */
  hint?: string;
  className?: string;
  /** 数值文本的额外类名——例如语义色调色。 */
  valueClassName?: string;
}

const TREND_CLASS: Record<StatDelta["trend"], string> = {
  up: "text-success",
  down: "text-destructive",
  neutral: "text-muted-foreground",
};

/** KPI 卡片：标签 + 大号数值 + 可选的图标、变化量与说明。 */
export function StatCard({
  label,
  value,
  icon: Icon,
  delta,
  hint,
  className,
  valueClassName,
}: StatCardProps) {
  return (
    <div
      data-slot="stat-card"
      className={cn(
        "rounded-xl bg-card p-4 text-card-foreground ring-1 ring-foreground/10",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </p>
        {Icon && (
          <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        )}
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className={cn("text-2xl font-semibold tracking-tight tabular-nums", valueClassName)}>
          {value}
        </span>
        {delta && (
          <span className={cn("text-xs font-medium", TREND_CLASS[delta.trend])}>
            {delta.value}
          </span>
        )}
      </div>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
