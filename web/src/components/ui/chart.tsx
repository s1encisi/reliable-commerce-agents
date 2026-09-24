"use client";

import { ResponsiveContainer } from "recharts";
import { cn } from "@/lib/utils";

/**
 * 图表配色令牌，绑定 globals.css 中的 OKLCH `--chart-*` CSS 变量，
 * 使图表能自动适配浅色 / 深色主题。按索引取用。
 */
export const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
] as const;

export interface ChartContainerProps {
  /** 单个 recharts 图表元素（例如 <LineChart>...</LineChart>）。 */
  children: React.ReactElement;
  /** 固定像素高度；宽度始终自适应。 */
  height?: number;
  className?: string;
}

/**
 * recharts 的自适应容器包装。保持固定高度（recharts 需要一个有界的盒子），
 * 宽度则撑满父元素。
 */
export function ChartContainer({
  children,
  height = 240,
  className,
}: ChartContainerProps) {
  return (
    <div
      data-slot="chart"
      className={cn("w-full", className)}
      style={{ height }}
    >
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}
