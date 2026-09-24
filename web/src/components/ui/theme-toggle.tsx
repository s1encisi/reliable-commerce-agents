"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

type Theme = "light" | "dark";

/** 每当 <html> 的 class 属性变化时重新读取主题。 */
function subscribe(callback: () => void): () => void {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class"],
  });
  return () => observer.disconnect();
}

function getSnapshot(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

/** 服务端渲染默认按浅色处理；随后由内联初始化脚本与客户端 store 修正。 */
function getServerSnapshot(): Theme {
  return "light";
}

/**
 * 浅色 / 深色主题切换按钮。状态通过 useSyncExternalStore 从 <html> 的 `dark`
 * 类读取（不依赖 effect-setState，也不会产生 hydration 不一致）；初始类由根
 * 布局的初始化脚本在首次绘制前写入。
 */
export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem("theme", next);
    } catch {
      // 忽略存储失败（无痕模式等）
    }
  }

  const nextLabel = theme === "dark" ? "浅色" : "深色";

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label={`切换到${nextLabel}模式`}
      title={`切换到${nextLabel}模式`}
    >
      {theme === "dark" ? (
        <Sun className="size-4" />
      ) : (
        <Moon className="size-4" />
      )}
    </Button>
  );
}
