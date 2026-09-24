import { cn } from "@/lib/utils";

/**
 * 加载占位块。通过 className 指定尺寸，例如
 * `<Skeleton className="h-4 w-32" />`。
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  );
}

export { Skeleton };
