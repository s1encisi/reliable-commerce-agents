import { cn } from "@/lib/utils";

export interface SectionHeaderProps {
  title: string;
  description?: string;
  /** 标题上方的小号大写标签。 */
  eyebrow?: string;
  /** 右对齐插槽，例如按钮或筛选器。 */
  action?: React.ReactNode;
  className?: string;
}

/** 应用外壳中页面 / 区块的统一标题栏。 */
export function SectionHeader({
  title,
  description,
  eyebrow,
  action,
  className,
}: SectionHeaderProps) {
  return (
    <div className={cn("mb-4 flex items-start justify-between gap-4", className)}>
      <div className="space-y-1">
        {eyebrow && (
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {eyebrow}
          </p>
        )}
        <h2 className="font-heading text-lg font-semibold tracking-tight">
          {title}
        </h2>
        {description && (
          <p className="text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
