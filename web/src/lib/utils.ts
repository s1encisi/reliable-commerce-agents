import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

/** 合并 className：先用 clsx 处理条件类名，再由 tailwind-merge 消解 Tailwind 冲突。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
