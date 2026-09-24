"use client";

import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
} from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api, type AgentStep, type GroundingReport } from "@/lib/api";
import { RichMessage } from "@/components/chat/rich-message";
import { AgentTimeline } from "@/components/chat/agent-timeline";
import { GroundingBadge } from "@/components/chat/grounding-badge";
import { OrchestrationGraph } from "@/components/chat/orchestration-graph";
import { ModeSwitcher } from "@/components/chat/mode-switcher";
import { ModeComparison } from "@/components/chat/mode-comparison";
import { QUICK_PROMPTS } from "@/lib/scenarios";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PromptInputBox } from "@/components/ui/ai-prompt-box";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  Sheet,
  SheetTrigger,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import {
  MessageSquarePlusIcon,
  Trash2Icon,
  PanelLeftIcon,
  BotIcon,
  UserIcon,
  ShoppingCart,
  Copy,
  Check,
  RotateCcw,
  Share2,
  Sparkles,
} from "lucide-react";
import { ApprovalCard } from "@/components/chat/approval-card";
import { DEMO_SCENARIOS } from "@/lib/scenarios";
import { deriveSuggestions } from "@/lib/suggestions";
import { AGENT_MODES } from "@/components/ui/ai-prompt-box";

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  agents_involved?: string[];
  steps?: AgentStep[];
  created_at?: string;
  streaming?: boolean;
  /** 本轮所走的编排模式——挂在助手消息上，使 OrchestrationGraph 知道该渲染
   * 哪个模式的图，而不受切换器当前选择的影响。 */
  mode?: string;
  /** 本轮记录所在的 `usage_logs` id——来自 SSE 的 `event: run` 帧；该帧在持久化
   * 之后才到达，因为在此之前 id 尚不存在。用于恢复一个因等待人工决策而暂停的
   * 运行。 */
  runId?: string;
  /** 当运行停在工作流内的人工参与（HITL）关卡并等待决策时置位。驱动内联审批卡。 */
  pendingApproval?: boolean;
  /** 当前运行中 / 已完成 / 出错的执行器 id（连字符形式，实时值）——来自
   * `event: node` / `error` SSE 帧。 */
  activeNodeIds?: string[];
  doneNodeIds?: string[];
  errorNodeIds?: string[];
  /** 来自 `event: grounding` 的服务端事实核验报告（仅工具模式，且
   * GROUNDING_MODE != off）。 */
  grounding?: GroundingReport;
}

interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// 编排模式持久化——按会话记忆（尚未创建的会话记在 "draft" 键下），因此刷新后
// 仍保持同一模式选中。它与 ai-prompt-box.tsx 中的 AGENT_MODES / agentMode 不同：
// 后者用于直接指定某个专业智能体，这里决定的是编排器本身以何种方式运行本轮。
// ---------------------------------------------------------------------------

const ORCH_MODE_STORAGE_PREFIX = "ecommerce_orch_mode:";
const ORCH_MODE_DRAFT_KEY = "draft";

function loadStoredOrchestrationMode(conversationId: string | null): string {
  if (typeof window === "undefined") return "";
  try {
    return localStorage.getItem(ORCH_MODE_STORAGE_PREFIX + (conversationId ?? ORCH_MODE_DRAFT_KEY)) ?? "";
  } catch {
    return "";
  }
}

function storeOrchestrationMode(conversationId: string | null, mode: string): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(ORCH_MODE_STORAGE_PREFIX + (conversationId ?? ORCH_MODE_DRAFT_KEY), mode);
  } catch {
    // 无痕浏览 / 存储已满——模式在本会话内仍通过组件状态生效，只是无法在刷新后保留。
  }
}

// ---------------------------------------------------------------------------
// 思考中指示器（替代通用的打字省略号）
// ---------------------------------------------------------------------------

function ThinkingIndicator({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 px-1 py-3">
      <Avatar className="size-7 shrink-0">
        <AvatarFallback className="bg-muted text-muted-foreground text-xs">
          <BotIcon className="size-3.5" />
        </AvatarFallback>
      </Avatar>
      <motion.div
        className="flex items-center gap-1.5 text-xs text-muted-foreground"
        animate={{ opacity: [0.5, 1, 0.5] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
      >
        <Sparkles className="size-3 text-primary/70" />
        <span>{label}</span>
      </motion.div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 消息操作栏（复制、重试、分享）
// ---------------------------------------------------------------------------

function MessageActions({
  text,
  onRetry,
}: {
  text: string;
  onRetry: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [shared, setShared] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* 剪贴板不可用 */ }
  };

  const handleShare = async () => {
    try {
      const url = `${window.location.origin}/chat?prompt=${encodeURIComponent(text.slice(0, 200))}`;
      await navigator.clipboard.writeText(url);
      setShared(true);
      setTimeout(() => setShared(false), 1500);
    } catch { /* 剪贴板不可用 */ }
  };

  return (
    <div className="mt-1.5 flex items-center gap-3">
      <button
        type="button"
        onClick={handleCopy}
        className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
      >
        {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
        {copied ? "已复制" : "复制"}
      </button>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
      >
        <RotateCcw className="size-3" />
        重试
      </button>
      <button
        type="button"
        onClick={handleShare}
        className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
      >
        {shared ? <Check className="size-3" /> : <Share2 className="size-3" />}
        {shared ? "已复制链接" : "分享"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 会话列表（桌面端面板与移动端抽屉共用）
// ---------------------------------------------------------------------------

function ConversationList({
  conversations,
  activeId,
  onSelect,
  onDelete,
  onNew,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-3 py-2.5">
        <span className="text-sm font-semibold text-foreground">
          会话
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onNew}
          title="新对话"
        >
          <MessageSquarePlusIcon className="size-4" />
        </Button>
      </div>
      <Separator />
      <ScrollArea className="flex-1 overflow-y-auto">
        <div className="flex flex-col gap-0.5 p-1.5">
          {conversations.length === 0 && (
            <p className="px-2 py-8 text-center text-xs text-muted-foreground">
              还没有会话。发送一条消息即可开始。
            </p>
          )}
          {conversations.map((conv) => (
            <div
              key={conv.id}
              className={`group flex items-center gap-1 rounded-md px-2.5 py-1.5 text-sm cursor-pointer transition-colors ${
                conv.id === activeId
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              }`}
              onClick={() => onSelect(conv.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onSelect(conv.id);
              }}
              role="button"
              tabIndex={0}
            >
              <span className="flex-1 truncate">{conv.title}</span>
              <Button
                variant="ghost"
                size="icon-xs"
                className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0 text-muted-foreground hover:text-destructive"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(conv.id);
                }}
                title="删除会话"
              >
                <Trash2Icon className="size-3" />
              </Button>
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 消息内容渲染器
// ---------------------------------------------------------------------------

function MessageContent({ content }: { content: string }) {
  const paragraphs = content.split("\n\n");
  if (paragraphs.length <= 1) {
    return <span className="whitespace-pre-wrap">{content}</span>;
  }
  return (
    <>
      {paragraphs.map((p, i) => (
        <p
          key={i}
          className={i > 0 ? "mt-2 whitespace-pre-wrap" : "whitespace-pre-wrap"}
        >
          {p}
        </p>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// 对话页
// ---------------------------------------------------------------------------

export default function ChatPage() {
  const { isAuthenticated } = useAuth();
  const { itemCount } = useCart();

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isResponding, setIsResponding] = useState(false);
  const [thinkingLabel, setThinkingLabel] = useState<string>("正在路由到专业智能体…");
  const [sheetOpen, setSheetOpen] = useState(false);
  const [orchestrationMode, setOrchestrationMode] = useState<string>("");

  const searchParams = useSearchParams();
  const pendingQueryRef = useRef<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  /**
   * 追问胶囊，由助手最后一条已完成的消息推导而来。
   *
   * 原先写死为 `DEMO_SCENARIOS.slice(0, 4)`——每轮之后都是同样四条固定提示词，
   * 即便助手刚刚提出了一个直接问题，胶囊也对此视而不见（issue #4）。
   *
   * 仍在流式输出的消息会被跳过：从半截回答推导会让胶囊闪烁，更糟的是会短暂
   * 展示一张尚未渲染完成的卡片的建议。
   *
   * `deriveSuggestions` 是作用于客户端已有文本的纯函数——不发请求、没有延迟，
   * 且可独立测试（`lib/suggestions.test.ts`）。
   */
  const suggestions = useMemo(() => {
    const lastAssistant = [...messages]
      .reverse()
      .find((m) => m.role === "assistant" && !m.streaming);

    return deriveSuggestions(
      lastAssistant?.content,
      DEMO_SCENARIOS.slice(0, 4).map((s) => ({ label: s.label, prompt: s.prompt }))
    );
  }, [messages]);
  // 每条消息各自的取消控制器。新的发送会中止任何进行中的流；组件卸载时中止
  // 仍在运行的那个。用于封堵 SSE 泄漏问题。
  const streamAbortRef = useRef<AbortController | null>(null);
  // 由 sendMessage() 在创建新会话后立即设置，使下方「活动会话变化时加载消息」
  // 的 effect 能对该次切换跳过常规的服务端重载——详见该 effect 的注释。
  const justCreatedConversationRef = useRef<string | null>(null);

  // 组件卸载（路由切换）时取消进行中的流。
  useEffect(
    () => () => {
      streamAbortRef.current?.abort();
    },
    []
  );

  // ---- 挂载时加载会话列表 ----
  useEffect(() => {
    if (!isAuthenticated) return;
    loadConversations();
  }, [isAuthenticated]);

  // ---- 挂载时自动发送 ?q= 查询参数 ----
  useEffect(() => {
    if (!isAuthenticated) return;
    // `prompt` 是来自首页快捷提示词 / 商品页的深链参数；`q` 为向后兼容保留。
    const q = searchParams.get("prompt") ?? searchParams.get("q");
    if (q) {
      pendingQueryRef.current = q;
    }
  }, [isAuthenticated, searchParams]);

  // 会话加载完成后触发待发送的查询（首次挂载）
  useEffect(() => {
    if (pendingQueryRef.current && !isResponding) {
      const q = pendingQueryRef.current;
      pendingQueryRef.current = null;
      sendMessage(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversations]);

  async function loadConversations() {
    try {
      const data = await api.getConversations();
      setConversations(data);
    } catch {
      // 静默处理——首次加载时列表为空是正常的
    }
  }

  // ---- 活动会话变化时加载消息 ----
  useEffect(() => {
    if (!activeConversationId) {
      setMessages([]);
      return;
    }
    if (justCreatedConversationRef.current === activeConversationId) {
      // sendMessage() 在为它自己的首轮创建会话后刚设置了这个 id——本地
      // `messages` 状态已经完整（而且更丰富：它携带了仅客户端才有的字段，
      // 例如助手消息的 `mode` / `activeNodeIds`，OrchestrationGraph 需要它们，
      // 而服务端无处持久化）。此处从服务端重载会静默替换这些消息并抹掉那些
      // 字段——这是线上实际发现的：每个新会话首次发送后几百毫秒，图先渲染
      // 出来又消失。
      justCreatedConversationRef.current = null;
      return;
    }
    loadMessages(activeConversationId);
  }, [activeConversationId]);

  // ---- 恢复该会话记住的编排模式 ----
  useEffect(() => {
    setOrchestrationMode(loadStoredOrchestrationMode(activeConversationId));
  }, [activeConversationId]);

  const handleModeChange = useCallback(
    (mode: string) => {
      setOrchestrationMode(mode);
      storeOrchestrationMode(activeConversationId, mode);
    },
    [activeConversationId],
  );

  async function loadMessages(conversationId: string) {
    try {
      const data = await api.getConversation(conversationId);
      setMessages(data.messages ?? []);
    } catch {
      setMessages([]);
    }
  }

  // ---- 自动滚动到底部 ----
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isResponding]);

  // ---- 开始新会话 ----
  const handleNewChat = useCallback(() => {
    setActiveConversationId(null);
    setMessages([]);
    setSheetOpen(false);
  }, []);

  // ---- 选择会话 ----
  const handleSelectConversation = useCallback((id: string) => {
    setActiveConversationId(id);
    setSheetOpen(false);
  }, []);

  // ---- 删除会话 ----
  const handleDeleteConversation = useCallback(
    async (id: string) => {
      try {
        await api.deleteConversation(id);
        setConversations((prev) => prev.filter((c) => c.id !== id));
        if (activeConversationId === id) {
          setActiveConversationId(null);
          setMessages([]);
        }
      } catch {
        // 静默吞掉——无论成败，界面保持一致
      }
    },
    [activeConversationId],
  );

  // ---- 核心发送逻辑（表单提交与 ?q= 自动发送共用）----
  async function sendMessage(text: string, agentMode?: string | null) {
    if (!text.trim() || isResponding) return;

    const trimmed = text.trim();

    // 依据所选模式设置初始的思考提示
    const modeEntry = AGENT_MODES.find((m) => m.id === agentMode);
    setThinkingLabel(
      modeEntry && modeEntry.id
        ? `正在路由到${modeEntry.label}…`
        : "正在路由到专业智能体…"
    );

    // 乐观渲染用户消息
    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: trimmed,
    };
    setMessages((prev) => [...prev, userMessage]);
    setIsResponding(true);

    const assistantId = crypto.randomUUID();
    let assistantCreated = false;
    // 本轮编排器自身的最终文本（onChunk）开始到达后置位。在此之前展示的任何
    // 内容都是专业智能体的实时 `delta` 预览（onDeltaChunk）——第一个真实文本
    // 分片会*替换*它而不是追加，因为后端也不再把这个预览持久化进保存的消息
    // （Phase 8.1）：专业智能体的预览与编排器自身的最终回答用各自独立生成的
    // 措辞复述同一内容，同时展示会造成可见的重复，而不是两件真正不同的东西。
    let receivedFinalText = false;
    // 此处快照——本轮结束前 orchestrationMode 可能变化（例如用户在本轮仍在
    // 流式输出时为下一条消息选了另一个模式），但这条消息走的是发送那一刻所选
    // 的模式。
    const messageMode = orchestrationMode || "tool";

    // 启动新流之前，先中止仍在排空的旧流。
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;

    try {
      const meta = await api.chatStream(
        trimmed,
        activeConversationId ?? undefined,
        (chunk) => {
          const isFirstFinalChunk = !receivedFinalText;
          receivedFinalText = true;
          if (!assistantCreated) {
            assistantCreated = true;
            setMessages((prev) => [
              ...prev,
              {
                id: assistantId,
                role: "assistant",
                content: chunk,
                streaming: true,
                mode: messageMode,
              },
            ]);
          } else {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: isFirstFinalChunk ? chunk : m.content + chunk }
                  : m,
              ),
            );
          }
        },
        controller.signal,
        {
          onDeltaChunk: (chunk) => {
            // 仅作防御——MAF 自身的执行顺序保证专业智能体的 delta 块在编排器
            // 最终块开始前就已完成并关闭（已核实：二者从不交错），因此当
            // receivedFinalText 为 true 时这里不应真的触发。万一触发了，丢弃
            // 它也比把一个过期的预览重新追加到真实回答上更安全。
            if (receivedFinalText) return;
            if (!assistantCreated) {
              assistantCreated = true;
              setMessages((prev) => [
                ...prev,
                {
                  id: assistantId,
                  role: "assistant",
                  content: chunk,
                  streaming: true,
                  mode: messageMode,
                },
              ]);
            } else {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, content: m.content + chunk } : m,
                ),
              );
            }
          },
          onStep: (step) => {
            // 用当前活跃智能体更新思考提示
            const agent = step.agent ?? "orchestrator";
            const AGENT_LABELS: Record<string, string> = {
              orchestrator: "正在路由…",
              "product-discovery": "商品发现正在检索…",
              "order-management": "订单管理正在查询…",
              "pricing-promotions": "定价与促销正在计算…",
              "review-sentiment": "评论情感分析正在分析…",
              "inventory-fulfillment": "库存与履约正在检查库存…",
            };
            setThinkingLabel(AGENT_LABELS[agent] ?? `${agent} 正在处理…`);

            setMessages((prev) => {
              if (!prev.some((m) => m.id === assistantId)) {
                assistantCreated = true;
                return [
                  ...prev,
                  {
                    id: assistantId,
                    role: "assistant",
                    content: "",
                    streaming: true,
                    steps: [step],
                    mode: messageMode,
                  },
                ];
              }
              return prev.map((m) =>
                m.id === assistantId
                  ? { ...m, steps: [...(m.steps ?? []), step] }
                  : m,
              );
            });
          },
          onOrchestrationEvent: (eventName, data) => {
            if (eventName === "run") {
              const { run_id, pending_approval } = data as {
                run_id?: string;
                pending_approval?: boolean;
              };
              if (!run_id) return;
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, runId: run_id, pendingApproval: pending_approval === true }
                    : m,
                ),
              );
              return;
            }
            if (eventName !== "node" && eventName !== "error") return;
            const frame = data as { node_id?: string; phase?: "enter" | "exit" };
            const nodeId = frame.node_id;
            if (!nodeId) return;

            setMessages((prev) => {
              const existing = prev.find((m) => m.id === assistantId);
              const base: Message = existing ?? {
                id: assistantId,
                role: "assistant",
                content: "",
                streaming: true,
                mode: messageMode,
              };
              if (!existing) assistantCreated = true;

              let next: Message;
              if (eventName === "error") {
                next = { ...base, errorNodeIds: [...(base.errorNodeIds ?? []), nodeId] };
              } else if (frame.phase === "exit") {
                next = {
                  ...base,
                  activeNodeIds: (base.activeNodeIds ?? []).filter((id) => id !== nodeId),
                  doneNodeIds: [...(base.doneNodeIds ?? []), nodeId],
                };
              } else {
                next = { ...base, activeNodeIds: [...(base.activeNodeIds ?? []), nodeId] };
              }

              return existing ? prev.map((m) => (m.id === assistantId ? next : m)) : [...prev, next];
            });
          },
          onGrounding: (report) => {
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, grounding: report } : m)),
            );
          },
          mode: orchestrationMode || undefined,
        },
      );

      // 如果这是第一条消息，则已创建新会话——把存在 draft 键下的模式重新挂到
      // 真实会话 id 上，刷新后仍能取回。
      if (!activeConversationId && meta.conversation_id) {
        storeOrchestrationMode(meta.conversation_id, orchestrationMode);
        justCreatedConversationRef.current = meta.conversation_id;
        setActiveConversationId(meta.conversation_id);
        await loadConversations();
      }

      // 收尾：去掉流式标记，附上 agents_involved
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, streaming: false, agents_involved: meta.agents_involved }
            : m,
        ),
      );
    } catch (err) {
      const errMsg =
        err instanceof Error ? err.message : "出了点问题。";
      setMessages((prev) => {
        const existing = prev.find((m) => m.id === assistantId);
        if (existing) {
          return prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: `错误：${errMsg}`, streaming: false }
              : m,
          );
        }
        return [
          ...prev,
          { id: assistantId, role: "assistant", content: `错误：${errMsg}` },
        ];
      });
    } finally {
      setIsResponding(false);
    }
  }

  // ---- 渲染 ----
  return (
    <div className="flex h-full">
      {/* -------- 会话列表面板（桌面端）-------- */}
      <aside className="hidden w-60 shrink-0 flex-col border-r bg-muted/30 lg:flex">
        <ConversationList
          conversations={conversations}
          activeId={activeConversationId}
          onSelect={handleSelectConversation}
          onDelete={handleDeleteConversation}
          onNew={handleNewChat}
        />
      </aside>

      {/* -------- 主对话区 -------- */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* ---- 顶栏 ---- */}
        <div className="flex h-11 items-center gap-2 border-b px-3">
          {/* 移动端会话列表开关 */}
          <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
            <SheetTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="lg:hidden"
                />
              }
            >
              <PanelLeftIcon className="size-4" />
            </SheetTrigger>
            <SheetContent side="left" className="w-72 p-0">
              <SheetHeader className="sr-only">
                <SheetTitle>会话</SheetTitle>
              </SheetHeader>
              <ConversationList
                conversations={conversations}
                activeId={activeConversationId}
                onSelect={handleSelectConversation}
                onDelete={handleDeleteConversation}
                onNew={handleNewChat}
              />
            </SheetContent>
          </Sheet>

          <h2 className="flex-1 truncate text-sm font-medium">
            {activeConversationId
              ? conversations.find((c) => c.id === activeConversationId)
                  ?.title ?? "对话"
              : "新对话"}
          </h2>

          <Link
            href="/cart"
            className="relative flex items-center gap-1 rounded-lg px-2 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <ShoppingCart className="size-4" />
            {itemCount > 0 && (
              <span className="absolute -right-1 -top-1 flex size-4 items-center justify-center rounded-full bg-primary text-[9px] font-bold text-primary-foreground">
                {itemCount > 99 ? "99+" : itemCount}
              </span>
            )}
          </Link>

          <Button
            variant="ghost"
            size="icon-sm"
            className="lg:hidden"
            onClick={handleNewChat}
            title="新对话"
          >
            <MessageSquarePlusIcon className="size-4" />
          </Button>
        </div>

        {/* ---- 消息区 ---- */}
        <div className="flex-1 overflow-y-auto">
          {messages.length === 0 && !isResponding ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
              <div className="flex size-14 items-center justify-center rounded-full bg-primary/10">
                <BotIcon className="size-7 text-primary" />
              </div>
              <h3 className="text-base font-semibold">
                今天有什么可以帮您？
              </h3>
              <p className="max-w-sm text-sm text-muted-foreground">
                您可以咨询商品、订单、价格、评论、库存等任何问题，我们的专业智能体
                会协作帮您解决。
              </p>
              <div className="mt-2 flex flex-wrap justify-center gap-2">
                {QUICK_PROMPTS.map((s) => (
                  <button
                    key={s.label}
                    type="button"
                    onClick={() => sendMessage(s.prompt)}
                    className="rounded-full border bg-card px-3 py-1.5 text-sm text-foreground/80 transition-colors hover:border-primary/40 hover:bg-accent hover:text-foreground"
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl px-4 py-4">
              {messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`mb-4 flex items-start gap-2.5 ${
                    msg.role === "user" ? "flex-row-reverse" : "flex-row"
                  }`}
                >
                  <Avatar className="mt-0.5 size-7 shrink-0">
                    <AvatarFallback
                      className={
                        msg.role === "user"
                          ? "bg-primary text-primary-foreground text-xs"
                          : "bg-muted text-muted-foreground text-xs"
                      }
                    >
                      {msg.role === "user" ? (
                        <UserIcon className="size-3.5" />
                      ) : (
                        <BotIcon className="size-3.5" />
                      )}
                    </AvatarFallback>
                  </Avatar>

                  <div
                    className={`max-w-[80%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
                      msg.role === "user"
                        ? "bg-primary text-primary-foreground"
                        : "bg-muted text-foreground"
                    }`}
                  >
                    {msg.role === "assistant" ? (
                      <RichMessage
                        content={msg.content}
                        streaming={msg.streaming}
                        onAction={(text) => sendMessage(text)}
                      />
                    ) : (
                      <MessageContent content={msg.content} />
                    )}

                    {msg.agents_involved && msg.agents_involved.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {msg.agents_involved.map((agent) => (
                          <Badge
                            key={agent}
                            variant="outline"
                            className="text-[10px] font-normal"
                          >
                            {agent}
                          </Badge>
                        ))}
                      </div>
                    )}

                    {msg.role === "assistant" &&
                      msg.steps &&
                      msg.steps.length > 0 && <AgentTimeline steps={msg.steps} />}

                    {msg.role === "assistant" && <GroundingBadge report={msg.grounding} />}

                    {msg.role === "assistant" && msg.pendingApproval && msg.runId && (
                      <ApprovalCard
                        runId={msg.runId}
                        onResolved={(outcome) => {
                          // 清除这条消息上的关卡，并把恢复后的轮次作为独立的助手
                          // 消息追加，使整段对话读起来就是它真实的样子：暂停、
                          // 决策、继续。
                          setMessages((prev) => [
                            ...prev.map((m) =>
                              m.id === msg.id ? { ...m, pendingApproval: false } : m,
                            ),
                            {
                              id: `${msg.id}-resumed`,
                              role: "assistant" as const,
                              content: outcome.text,
                              agents_involved: outcome.agentsInvolved,
                            },
                          ]);
                        }}
                      />
                    )}

                    {msg.role === "assistant" && msg.mode && (
                      <OrchestrationGraph
                        mode={msg.mode}
                        activeNodeIds={msg.activeNodeIds}
                        doneNodeIds={msg.doneNodeIds}
                        errorNodeIds={msg.errorNodeIds}
                      />
                    )}

                    {msg.role === "assistant" && !msg.streaming && msg.content && (
                      <MessageActions
                        text={msg.content}
                        onRetry={() => {
                          // 找到紧邻这条助手消息之前的用户消息
                          const msgIndex = messages.findIndex((m) => m.id === msg.id);
                          const userMsg = messages
                            .slice(0, msgIndex)
                            .findLast((m) => m.role === "user");
                          if (userMsg) sendMessage(userMsg.content);
                        }}
                      />
                    )}
                  </div>
                </div>
              ))}

              <AnimatePresence>
                {isResponding && messages[messages.length - 1]?.role !== "assistant" && (
                  <motion.div
                    key="thinking"
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.15 }}
                  >
                    <ThinkingIndicator label={thinkingLabel} />
                  </motion.div>
                )}
              </AnimatePresence>

              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* ---- 输入区 ---- */}
        <div className="border-t bg-background px-4 py-3">
          <div className="mx-auto max-w-3xl space-y-2">
            <div className="flex items-center justify-end gap-2">
              <ModeComparison />
              <ModeSwitcher value={orchestrationMode} onChange={handleModeChange} disabled={isResponding} />
            </div>
            <PromptInputBox
              onSend={(message, agentMode) => sendMessage(message, agentMode)}
              isLoading={isResponding}
              suggestions={suggestions}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
