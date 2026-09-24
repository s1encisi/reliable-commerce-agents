import { StatCard, type StatCardProps } from "@/components/ui/stat-card";
import { TONE_TEXT_CLASS, type SemanticTone } from "@/components/ui/status-badge";
import { cn } from "@/lib/utils";

export interface StatTileProps extends StatCardProps {
  /** 用与 StatusBadge 相同的色调令牌为数值文本着色。 */
  tone?: SemanticTone;
}

/**
 * StatCard 的轻量包装，用于生成式 UI 中的标量提示（例如「库存：42 件」
 * 「风险等级：高」）——这类场景下数值本身带有语义色调。StatCard 自身没有
 * 色调概念，因此这里在其之上叠加，而不是复制一份卡片外观。
 */
export function StatTile({ tone, valueClassName, ...props }: StatTileProps) {
  return (
    <StatCard
      {...props}
      valueClassName={cn(tone && TONE_TEXT_CLASS[tone], valueClassName)}
    />
  );
}
