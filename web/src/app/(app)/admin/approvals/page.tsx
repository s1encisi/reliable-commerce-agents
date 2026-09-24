"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  CheckCircle,
  XCircle,
  Clock,
  ShieldAlert,
  Loader2,
  RefreshCw,
  Wrench,
  User,
} from "lucide-react";

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

type HitlRequest = {
  id: string;
  user_email: string;
  agent_name: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  status: string;
  admin_note: string | null;
  approved_by: string | null;
  execution_result: Record<string, unknown> | null;
  created_at: string;
  resolved_at: string | null;
};

// ---------------------------------------------------------------------------
// 辅助函数
// ---------------------------------------------------------------------------

const TOOL_LABELS: Record<string, string> = {
  cancel_order: "取消订单",
  process_refund: "处理退款",
  initiate_return: "发起退货",
  modify_order: "修改订单",
  place_backorder: "下单补货",
};

const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  pending: {
    label: "待审批",
    className: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-400",
  },
  approved: {
    label: "已批准",
    className: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-400",
  },
  executed: {
    label: "已执行",
    className: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-400",
  },
  denied: {
    label: "已拒绝",
    className: "border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-400",
  },
};

function formatTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function inputSummary(tool: string, input: Record<string, unknown>): string {
  if (tool === "cancel_order" || tool === "process_refund" || tool === "modify_order") {
    const id = String(input.order_id ?? "").slice(0, 8);
    const reason = input.reason ? ` —— 「${input.reason}」` : "";
    return `订单 #${id}${reason}`;
  }
  if (tool === "initiate_return") {
    const id = String(input.order_id ?? "").slice(0, 8);
    return `订单 #${id}`;
  }
  if (tool === "place_backorder") {
    return `商品 #${String(input.product_id ?? "").slice(0, 8)}`;
  }
  return JSON.stringify(input).slice(0, 60);
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export default function AdminApprovalsPage() {
  const router = useRouter();
  const { user, isAdmin, isLoading: authLoading } = useAuth();

  const [requests, setRequests] = useState<HitlRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const [processing, setProcessing] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!authLoading && !user) router.replace("/login");
  }, [user, authLoading, router]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getHitlRequests(filter === "pending" ? "pending" : undefined);
      setRequests(data?.requests ?? []);
    } catch {
      toast.error("审批请求加载失败");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    if (user && isAdmin) load();
  }, [user, isAdmin, load]);

  async function handleApprove(req: HitlRequest) {
    setProcessing((p) => new Set(p).add(req.id));
    try {
      const result = await api.approveHitlRequest(req.id);
      const execResult = result?.execution_result as Record<string, unknown> | undefined;
      const success = execResult?.success;
      const msg = String(execResult?.message ?? "操作已批准并执行。");
      if (success) {
        toast.success(`已批准：${TOOL_LABELS[req.tool_name] ?? req.tool_name}`, {
          description: msg,
        });
      } else {
        toast(`已批准 —— 但执行出现问题`, { description: msg });
      }
      await load();
    } catch {
      toast.error("批准请求失败");
    } finally {
      setProcessing((p) => {
        const next = new Set(p);
        next.delete(req.id);
        return next;
      });
    }
  }

  async function handleDeny(req: HitlRequest) {
    setProcessing((p) => new Set(p).add(req.id));
    try {
      await api.denyHitlRequest(req.id);
      toast(`已拒绝：${TOOL_LABELS[req.tool_name] ?? req.tool_name}`);
      await load();
    } catch {
      toast.error("拒绝请求失败");
    } finally {
      setProcessing((p) => {
        const next = new Set(p);
        next.delete(req.id);
        return next;
      });
    }
  }

  if (authLoading) return null;

  if (!isAdmin) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="mx-auto flex size-16 items-center justify-center rounded-full bg-destructive/10">
            <ShieldAlert className="size-8 text-destructive" />
          </div>
          <h2 className="mt-4 text-lg font-semibold">无权访问</h2>
          <p className="mt-1 text-sm text-muted-foreground">需要管理员权限。</p>
        </div>
      </div>
    );
  }

  const pendingCount = requests.filter((r) => r.status === "pending").length;

  return (
    <div className="min-h-screen bg-background">
      {/* 页头 */}
      <div className="border-b bg-card">
        <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
                <CheckCircle className="size-5 text-primary-foreground" />
              </div>
              <div>
                <h1 className="text-xl font-bold">
                  审批队列
                  {pendingCount > 0 && (
                    <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-sm font-medium text-amber-700 dark:bg-amber-950 dark:text-amber-400">
                      {pendingCount} 项待处理
                    </span>
                  )}
                </h1>
                <p className="text-sm text-muted-foreground">
                  高风险智能体操作的人工审批（Human-in-the-loop）
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex rounded-lg border bg-card">
                {(["pending", "all"] as const).map((f) => (
                  <button
                    key={f}
                    type="button"
                    onClick={() => setFilter(f)}
                    className={`px-3 py-1.5 text-xs font-medium transition-colors first:rounded-l-lg last:rounded-r-lg ${
                      filter === f
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {f === "pending" ? "待处理" : "全部"}
                  </button>
                ))}
              </div>
              <Button variant="outline" size="sm" onClick={load} className="gap-1.5">
                <RefreshCw className="size-3.5" />
                刷新
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* 内容区 */}
      <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
        {loading && (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="size-5 animate-spin text-primary" />
            <span className="ml-2 text-sm text-muted-foreground">加载中…</span>
          </div>
        )}

        {!loading && requests.length === 0 && (
          <div className="py-20 text-center">
            <CheckCircle className="mx-auto size-12 text-emerald-500" />
            <p className="mt-3 text-sm font-medium text-foreground">
              {filter === "pending" ? "暂无待审批项" : "暂无请求"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              高风险智能体操作（取消订单、退款、退货）将在此显示。
            </p>
          </div>
        )}

        {!loading && requests.length > 0 && (
          <div className="space-y-3">
            {requests.map((req) => {
              const statusCfg = STATUS_CONFIG[req.status] ?? STATUS_CONFIG.pending;
              const isPending = req.status === "pending";
              const isProcessing = processing.has(req.id);

              return (
                <div
                  key={req.id}
                  className="rounded-xl bg-card ring-1 ring-foreground/10"
                >
                  <div className="flex items-start gap-4 p-4">
                    {/* 图标 */}
                    <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
                      {isPending ? (
                        <Clock className="size-4 text-amber-500" />
                      ) : req.status === "denied" ? (
                        <XCircle className="size-4 text-destructive" />
                      ) : (
                        <CheckCircle className="size-4 text-emerald-500" />
                      )}
                    </div>

                    {/* 主体内容 */}
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-sm">
                          {TOOL_LABELS[req.tool_name] ?? req.tool_name}
                        </span>
                        <Badge variant="outline" className={statusCfg.className}>
                          {statusCfg.label}
                        </Badge>
                      </div>

                      {/* 摘要 */}
                      <p className="mt-1 text-sm text-muted-foreground">
                        {inputSummary(req.tool_name, req.tool_input)}
                      </p>

                      {/* 元信息 */}
                      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <User className="size-3" />
                          {req.user_email}
                        </span>
                        <span className="flex items-center gap-1">
                          <Wrench className="size-3" />
                          {req.agent_name}
                        </span>
                        <span>{formatTs(req.created_at)}</span>
                        {req.resolved_at && (
                          <span>→ 已于 {formatTs(req.resolved_at)} 处理</span>
                        )}
                        {req.approved_by && (
                          <span>由 {req.approved_by} 处理</span>
                        )}
                      </div>

                      {/* 工具输入详情 */}
                      <details className="mt-2">
                        <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                          查看输入参数
                        </summary>
                        <pre className="mt-1.5 max-h-32 overflow-auto rounded bg-muted/60 px-2 py-1.5 text-[10px] leading-relaxed text-foreground/70">
                          {JSON.stringify(req.tool_input, null, 2)}
                        </pre>
                      </details>

                      {/* 执行结果 */}
                      {req.execution_result && (
                        <div className="mt-2 rounded-md bg-emerald-50 px-3 py-1.5 text-xs text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400">
                          {String(req.execution_result.message ?? JSON.stringify(req.execution_result))}
                        </div>
                      )}
                    </div>

                    {/* 操作按钮 */}
                    {isPending && (
                      <div className="flex shrink-0 gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={isProcessing}
                          onClick={() => handleDeny(req)}
                          className="gap-1.5 border-red-200 text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-400"
                        >
                          {isProcessing ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <XCircle className="size-3.5" />
                          )}
                          拒绝
                        </Button>
                        <Button
                          size="sm"
                          disabled={isProcessing}
                          onClick={() => handleApprove(req)}
                          className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700"
                        >
                          {isProcessing ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <CheckCircle className="size-3.5" />
                          )}
                          批准
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
