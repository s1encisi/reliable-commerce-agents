"use client";

import React from "react";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { ArrowUp, Check, ChevronDown, Paperclip, Square, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";

// ─── 提示气泡 ─────────────────────────────────────────────────────────────────

const TooltipProvider = TooltipPrimitive.Provider;
const Tooltip = TooltipPrimitive.Root;
const TooltipTrigger = TooltipPrimitive.Trigger;

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 6, ...props }, ref) => (
  <TooltipPrimitive.Content
    ref={ref}
    sideOffset={sideOffset}
    className={cn(
      "z-50 rounded-md border border-border bg-popover px-2.5 py-1 text-xs text-popover-foreground shadow-sm",
      "animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
      className
    )}
    {...props}
  />
));
TooltipContent.displayName = "TooltipContent";

// ─── 图片预览浮层 ─────────────────────────────────────────────────────────────

function ImagePreviewOverlay({ src, onClose }: { src: string; onClose: () => void }) {
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="relative max-w-[90vw]" onClick={(e) => e.stopPropagation()}>
        <img src={src} alt="预览" className="max-h-[85vh] max-w-full rounded-xl object-contain shadow-2xl" />
        <button onClick={onClose} className="absolute -right-2 -top-2 rounded-full bg-zinc-800 p-1.5 text-white transition-colors hover:bg-zinc-700">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

// ─── 智能体模式定义 ───────────────────────────────────────────────────────────

export interface AgentMode {
  id: string | null;
  label: string;
  placeholder: string;
}

// 注意：`id` 是后端智能体名，必须保持英文；`label` 与 `placeholder` 面向用户。
export const AGENT_MODES: AgentMode[] = [
  { id: null, label: "自动", placeholder: "询问商品、订单，或任何其他问题…" },
  { id: "product-discovery", label: "商品", placeholder: "搜索、对比或浏览商品目录…" },
  { id: "order-management", label: "订单", placeholder: "查询物流、取消或退掉订单…" },
  { id: "pricing-promotions", label: "定价", placeholder: "查看优惠、优惠券或价格趋势…" },
  { id: "review-sentiment", label: "评论", placeholder: "分析评论或查看情感倾向…" },
  { id: "inventory-fulfillment", label: "库存", placeholder: "查询库存或预估配送时间…" },
];

// ─── PromptInputBox ───────────────────────────────────────────────────────────

export interface PromptInputBoxProps {
  onSend: (message: string, agentMode?: string | null) => void;
  isLoading?: boolean;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  suggestions?: { label: string; prompt: string }[];
}

export const PromptInputBox = React.forwardRef<HTMLDivElement, PromptInputBoxProps>(
  ({ onSend, isLoading = false, className, disabled = false, suggestions = [] }, ref) => {
    const [input, setInput] = React.useState("");
    const [agentMode, setAgentMode] = React.useState<string | null>(null);
    const [attachedFile, setAttachedFile] = React.useState<File | null>(null);
    const [filePreview, setFilePreview] = React.useState<string | null>(null);
    const [selectedImage, setSelectedImage] = React.useState<string | null>(null);
    const [modeMenuOpen, setModeMenuOpen] = React.useState(false);
    const modeMenuRef = React.useRef<HTMLDivElement>(null);
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);
    const fileInputRef = React.useRef<HTMLInputElement>(null);
    const MAX_HEIGHT = 240;

    const currentMode = AGENT_MODES.find((m) => m.id === agentMode) ?? AGENT_MODES[0];
    const placeholder = currentMode.placeholder;

    // 输入框高度自适应
    React.useEffect(() => {
      const ta = textareaRef.current;
      if (!ta) return;
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, MAX_HEIGHT)}px`;
    }, [input]);

    // 点击外部或按 Escape 关闭模式菜单。如果弹层只能靠再次点击触发按钮来关闭，
    // 那些「误触打开」的用户就被困住了——而这恰恰是最常见的打开方式。
    React.useEffect(() => {
      if (!modeMenuOpen) return;
      const onPointerDown = (e: PointerEvent) => {
        if (!modeMenuRef.current?.contains(e.target as Node)) setModeMenuOpen(false);
      };
      const onKeyDown = (e: KeyboardEvent) => {
        if (e.key === "Escape") setModeMenuOpen(false);
      };
      document.addEventListener("pointerdown", onPointerDown);
      document.addEventListener("keydown", onKeyDown);
      return () => {
        document.removeEventListener("pointerdown", onPointerDown);
        document.removeEventListener("keydown", onKeyDown);
      };
    }, [modeMenuOpen]);

    const isDisabled = disabled || isLoading;
    const hasContent = input.trim() !== "" || attachedFile !== null;

    const handleSubmit = () => {
      if (!hasContent || isDisabled) return;
      onSend(input.trim(), agentMode);
      setInput("");
      setAttachedFile(null);
      setFilePreview(null);
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    };

    const attachFile = (file: File) => {
      if (!file.type.startsWith("image/") || file.size > 10 * 1024 * 1024) return;
      setAttachedFile(file);
      const reader = new FileReader();
      reader.onload = (ev) => setFilePreview(ev.target?.result as string);
      reader.readAsDataURL(file);
    };

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) attachFile(file);
      e.target.value = "";
    };

    const removeFile = () => { setAttachedFile(null); setFilePreview(null); };

    // 粘贴图片
    React.useEffect(() => {
      const handler = (e: ClipboardEvent) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        for (const item of Array.from(items)) {
          if (item.type.startsWith("image/")) {
            const file = item.getAsFile();
            if (file) { e.preventDefault(); attachFile(file); }
            break;
          }
        }
      };
      document.addEventListener("paste", handler);
      return () => document.removeEventListener("paste", handler);
    }, []);

    const handleDrop = (e: React.DragEvent) => {
      e.preventDefault();
      const file = Array.from(e.dataTransfer.files).find((f) => f.type.startsWith("image/"));
      if (file) attachFile(file);
    };

    return (
      <>
        <TooltipProvider>
          <div
            ref={ref}
            className={cn(
              "rounded-2xl border bg-background shadow-sm transition-colors duration-200",
              isLoading ? "border-teal-400/60" : "border-border hover:border-zinc-300",
              className
            )}
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDrop}
          >
            {/* 已附图片缩略图 */}
            <AnimatePresence>
              {filePreview && attachedFile && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.18 }}
                  className="overflow-hidden px-3 pt-2"
                >
                  <div className="relative inline-block">
                    <button type="button" className="block h-16 w-16 overflow-hidden rounded-lg" onClick={() => setSelectedImage(filePreview)}>
                      <img src={filePreview} alt={attachedFile.name} className="h-full w-full object-cover" />
                    </button>
                    <button type="button" onClick={removeFile} className="absolute -right-1.5 -top-1.5 rounded-full bg-zinc-700 p-0.5 text-white transition-colors hover:bg-zinc-600">
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* 文本输入区 */}
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={placeholder}
              disabled={isDisabled}
              rows={1}
              className={cn(
                "w-full resize-none bg-transparent px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground",
                "focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60",
                "min-h-[44px] overflow-y-auto leading-relaxed"
              )}
              style={{ maxHeight: MAX_HEIGHT, scrollbarWidth: "thin" }}
            />

            {/* 建议提问——仅在输入为空时显示 */}
            <AnimatePresence>
              {!input && suggestions.length > 0 && !isLoading && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.15 }}
                  className="overflow-hidden px-3 pb-1"
                >
                  <div className="flex flex-wrap gap-1.5">
                    {suggestions.slice(0, 3).map((s) => (
                      <button
                        key={s.label}
                        type="button"
                        onClick={() => { setInput(s.prompt); textareaRef.current?.focus(); }}
                        className="rounded-full border border-border/60 bg-muted/40 px-2.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-primary/30 hover:bg-muted hover:text-foreground"
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* 底部操作区 */}
            <div className="flex items-center justify-between px-3 pb-3">
              {/* 附件按钮 */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isDisabled}
                    className="flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
                  >
                    <Paperclip className="h-4 w-4" />
                  </button>
                </TooltipTrigger>
                <TooltipContent>上传图片</TooltipContent>
              </Tooltip>

              <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileChange} />

              {/*
                专业智能体选择器（折叠态）。

                这里原本是固定在输入框上方的一整行六个胶囊按钮。`自动` 才是默认
                项，也是绝大多数人想要的——正是它让编排器替你路由——而那一行在
                每个视口上都占掉一行输入区高度，只为展示五个极少用到的选项。

                折叠成单个按钮并显示「当前生效的模式」，这样状态依然一眼可见，代价
                从一行降为一个按钮。未指定时才显示「自动」；一旦选定某个专业智能体，
                按钮就显示该智能体名——这才是值得暴露的状态。

                注意：这不是编排模式切换器。那是另一个控件
                （`chat/mode-switcher.tsx`，数据来自 GET /api/orchestration/modes），
                位于页面工具栏，issue #4 明确将其排除在范围之外。把那个也折叠起来
                会隐藏本仓库赖以存在的核心功能。
              */}
              <div className="relative" ref={modeMenuRef}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() => setModeMenuOpen((v) => !v)}
                      disabled={isDisabled}
                      aria-haspopup="listbox"
                      aria-expanded={modeMenuOpen}
                      aria-label="专业智能体"
                      className={cn(
                        "flex h-8 items-center gap-1 rounded-full px-2.5 text-[11px] font-medium transition-colors disabled:pointer-events-none disabled:opacity-40",
                        agentMode === null
                          ? "text-muted-foreground hover:bg-muted hover:text-foreground"
                          : "bg-primary text-primary-foreground hover:bg-primary/90"
                      )}
                    >
                      {currentMode.label}
                      <ChevronDown className={cn("h-3 w-3 transition-transform", modeMenuOpen && "rotate-180")} />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {agentMode === null ? "自动路由" : `已指定 ${currentMode.label}`}
                  </TooltipContent>
                </Tooltip>

                <AnimatePresence>
                  {modeMenuOpen && (
                    <motion.div
                      role="listbox"
                      aria-label="专业智能体"
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: 4 }}
                      transition={{ duration: 0.12 }}
                      className="absolute bottom-full left-0 z-50 mb-2 min-w-44 overflow-hidden rounded-lg border bg-popover p-1 shadow-md"
                    >
                      {AGENT_MODES.map((mode) => (
                        <button
                          key={String(mode.id)}
                          type="button"
                          role="option"
                          aria-selected={agentMode === mode.id}
                          onClick={() => {
                            setAgentMode(mode.id);
                            setModeMenuOpen(false);
                            textareaRef.current?.focus();
                          }}
                          className={cn(
                            "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors",
                            agentMode === mode.id
                              ? "bg-accent text-accent-foreground"
                              : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                          )}
                        >
                          {mode.label}
                          {agentMode === mode.id && <Check className="h-3 w-3 shrink-0" />}
                        </button>
                      ))}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>

              {/* 键盘提示 */}
              <span className="hidden select-none text-[10px] text-muted-foreground/50 sm:block">
                Enter 发送 · Shift+Enter 换行
              </span>

              {/* 发送 / 停止按钮 */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <motion.button
                    type="button"
                    onClick={handleSubmit}
                    disabled={!hasContent && !isLoading}
                    whileTap={{ scale: 0.88 }}
                    transition={{ type: "spring", stiffness: 400, damping: 17 }}
                    className={cn(
                      "flex h-8 w-8 items-center justify-center rounded-full transition-all duration-200",
                      isLoading
                        ? "bg-red-500 text-white hover:bg-red-600"
                        : hasContent
                          ? "bg-teal-600 text-white hover:bg-teal-700"
                          : "bg-muted text-muted-foreground"
                    )}
                  >
                    <AnimatePresence mode="wait" initial={false}>
                      {isLoading ? (
                        <motion.span key="stop" initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.7 }} transition={{ duration: 0.12 }}>
                          <Square className="h-3.5 w-3.5 fill-white" />
                        </motion.span>
                      ) : (
                        <motion.span key="send" initial={{ opacity: 0, scale: 0.7, y: 4 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.7, y: -4 }} transition={{ duration: 0.12 }}>
                          <ArrowUp className="h-4 w-4" />
                        </motion.span>
                      )}
                    </AnimatePresence>
                  </motion.button>
                </TooltipTrigger>
                <TooltipContent>{isLoading ? "停止生成" : "发送消息"}</TooltipContent>
              </Tooltip>
            </div>
          </div>
        </TooltipProvider>

        {selectedImage && <ImagePreviewOverlay src={selectedImage} onClose={() => setSelectedImage(null)} />}
      </>
    );
  }
);

PromptInputBox.displayName = "PromptInputBox";
