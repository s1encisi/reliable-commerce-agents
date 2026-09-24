"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  ShieldAlert,
  ScrollText,
  Wrench,
  Download,
  ExternalLink,
  Search,
  X,
} from "lucide-react";
import { AGENTS } from "@/lib/agents";

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

type AuditStep = {
  step_index: number;
  tool_name: string;
  tool_input: Record<string, unknown> | null;
  tool_output: Record<string, unknown> | null;
  status: string;
  duration_ms: number;
};

type AuditEntry = {
  id: string;
  agent_name: string;
  user_email: string | null;
  user_name: string | null;
  input_summary: string | null;
  tokens_in: number;
  tokens_out: number;
  tool_calls_count: number;
  duration_ms: number;
  status: "success" | "error";
  error_message: string | null;
  trace_id: string | null;
  created_at: string;
  steps: AuditStep[];
};

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

const PAGE_SIZE = 25;
const ALL_AGENTS = "全部智能体";
const AGENT_OPTIONS = [ALL_AGENTS, ...AGENTS.map((a) => a.backendName)];
/** Jaeger 界面地址；追踪详情路径为 `/trace/{traceId}`。 */
const JAEGER_BASE = "http://localhost:16686";

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------

function formatNumber(n: number | undefined | null): string {
  if (n == null) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function formatDuration(ms: number | undefined | null): string {
  if (ms == null) return "0ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function formatTimestamp(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleString("zh-CN", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return dateStr;
  }
}

function exportJson(entries: AuditEntry[]) {
  const blob = new Blob([JSON.stringify(entries, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// 子组件
// ---------------------------------------------------------------------------

function FilterBar({
  agentFilter,
  setAgentFilter,
  statusFilter,
  setStatusFilter,
  search,
  setSearch,
}: {
  agentFilter: string;
  setAgentFilter: (v: string) => void;
  statusFilter: string;
  setStatusFilter: (v: string) => void;
  search: string;
  setSearch: (v: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-3">
      {/* 智能体筛选 */}
      <select
        value={agentFilter}
        onChange={(e) => setAgentFilter(e.target.value)}
        className="rounded-md border bg-background px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
        aria-label="按智能体筛选"
      >
        {AGENT_OPTIONS.map((a) => (
          <option key={a} value={a === ALL_AGENTS ? "" : a}>
            {a}
          </option>
        ))}
      </select>

      {/* 状态筛选 */}
      <select
        value={statusFilter}
        onChange={(e) => setStatusFilter(e.target.value)}
        className="rounded-md border bg-background px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
        aria-label="按状态筛选"
      >
        <option value="">全部状态</option>
        <option value="success">成功</option>
        <option value="error">失败</option>
      </select>

      {/* 搜索 */}
      <div className="relative flex-1 min-w-[180px]">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <input
          ref={inputRef}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索用户或提示词…"
          className="h-8 w-full rounded-md border bg-background pl-8 pr-8 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
        {search && (
          <button
            type="button"
            onClick={() => {
              setSearch("");
              inputRef.current?.focus();
            }}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

/** 单条工具调用的输入 / 输出详情。 */
function StepDetail({ step }: { step: AuditStep }) {
  const inputStr = step.tool_input
    ? JSON.stringify(step.tool_input, null, 2)
    : null;
  const outputStr = step.tool_output
    ? JSON.stringify(step.tool_output, null, 2)
    : null;

  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="inline-flex size-5 items-center justify-center rounded bg-muted text-[10px] font-medium text-muted-foreground">
            {step.step_index + 1}
          </span>
          <span className="font-mono text-xs font-semibold text-foreground/80">
            {step.tool_name}
          </span>
          {step.status === "error" && (
            <Badge
              variant="outline"
              className="border-red-200 bg-red-50 text-[10px] text-red-700"
            >
              错误
            </Badge>
          )}
        </div>
        <span className="text-xs text-muted-foreground">
          {formatDuration(step.duration_ms)}
        </span>
      </div>
      {inputStr && (
        <pre className="mt-1.5 max-h-24 overflow-auto rounded bg-muted/60 px-2 py-1 pl-7 text-[10px] leading-relaxed text-foreground/70">
          {inputStr}
        </pre>
      )}
      {outputStr && (
        <pre className="mt-1 max-h-24 overflow-auto rounded bg-muted/40 px-2 py-1 pl-7 text-[10px] leading-relaxed text-muted-foreground">
          {outputStr}
        </pre>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export default function AdminAuditPage() {
  const router = useRouter();
  const { user, isAdmin, isLoading: authLoading } = useAuth();

  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  // 筛选条件
  const [agentFilter, setAgentFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  // 防抖后的搜索词
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  // 筛选条件变化时回到第一页
  useEffect(() => {
    setPage(0);
  }, [agentFilter, statusFilter, debouncedSearch]);

  useEffect(() => {
    if (!authLoading && !user) router.replace("/login");
  }, [user, authLoading, router]);

  const loadAudit = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getAuditLog({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        agent_name: agentFilter || undefined,
        status: statusFilter || undefined,
        search: debouncedSearch || undefined,
      });
      setEntries(data?.entries ?? []);
      setTotal(data?.total ?? 0);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "加载审计日志失败",
      );
    } finally {
      setLoading(false);
    }
  }, [page, agentFilter, statusFilter, debouncedSearch]);

  useEffect(() => {
    if (user && isAdmin) loadAudit();
  }, [user, isAdmin, loadAudit]);

  function toggleExpanded(id: string) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
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
          <p className="mt-1 text-sm text-muted-foreground">
            需要管理员权限。
          </p>
        </div>
      </div>
    );
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasFilters = !!(agentFilter || statusFilter || debouncedSearch);

  return (
    <div className="min-h-screen bg-background">
      {/* 页头 */}
      <div className="border-b bg-card">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
                <ScrollText className="size-5 text-primary-foreground" />
              </div>
              <div>
                <h1 className="text-xl font-bold">智能体运行记录</h1>
                <p className="text-sm text-muted-foreground">
                  完整执行追踪——每一次智能体调用及其工具步骤
                </p>
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={entries.length === 0}
              onClick={() => exportJson(entries)}
              className="gap-1.5"
            >
              <Download className="size-3.5" />
              导出 JSON
            </Button>
          </div>
        </div>
      </div>

      {/* 内容区 */}
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <div className="rounded-xl bg-card ring-1 ring-foreground/10">
          {/* 筛选栏 */}
          <FilterBar
            agentFilter={agentFilter}
            setAgentFilter={setAgentFilter}
            statusFilter={statusFilter}
            setStatusFilter={setStatusFilter}
            search={search}
            setSearch={setSearch}
          />
          <Separator />

          {/* 表格上方统计行 */}
          <div className="flex items-center justify-between px-4 py-2">
            <span className="text-xs text-muted-foreground">
              {loading
                ? "加载中…"
                : `共 ${total.toLocaleString()} 条运行记录${hasFilters ? "（已筛选）" : ""}`}
            </span>
            {hasFilters && (
              <button
                type="button"
                onClick={() => {
                  setAgentFilter("");
                  setStatusFilter("");
                  setSearch("");
                }}
                className="text-xs text-primary hover:underline"
              >
                清除筛选
              </button>
            )}
          </div>

          {loading && (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="size-5 animate-spin text-primary" />
              <span className="ml-2 text-sm text-muted-foreground">
                加载中…
              </span>
            </div>
          )}

          {error && (
            <div className="mx-4 mb-4 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {!loading && !error && entries.length === 0 && (
            <div className="py-16 text-center">
              <ScrollText className="mx-auto size-10 text-muted-foreground" />
              <p className="mt-3 text-sm text-muted-foreground">
                {hasFilters
                  ? "没有符合当前筛选条件的运行记录。"
                  : "尚无运行记录。"}
              </p>
            </div>
          )}

          {!loading && !error && entries.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-[32px]" />
                  <TableHead>时间</TableHead>
                  <TableHead>智能体</TableHead>
                  <TableHead>用户</TableHead>
                  <TableHead>提示词</TableHead>
                  <TableHead className="text-right">Token</TableHead>
                  <TableHead className="text-right">耗时</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>追踪</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {entries.map((entry) => {
                  const isExpanded = expandedIds.has(entry.id);
                  const hasSteps = entry.steps?.length > 0;

                  return (
                    <TableRow key={entry.id} className="group" aria-expanded={isExpanded}>
                      <TableCell colSpan={9} className="p-0">
                        {/* 主行 */}
                        <div
                          className={`flex items-center text-sm ${hasSteps ? "cursor-pointer hover:bg-muted/30" : ""}`}
                          onClick={() => hasSteps && toggleExpanded(entry.id)}
                          onKeyDown={(e) => {
                            if (hasSteps && e.key === "Enter")
                              toggleExpanded(entry.id);
                          }}
                          role={hasSteps ? "button" : undefined}
                          tabIndex={hasSteps ? 0 : undefined}
                        >
                          {/* 展开开关 */}
                          <div className="flex w-10 shrink-0 items-center justify-center py-2.5">
                            {hasSteps ? (
                              isExpanded ? (
                                <ChevronDown className="size-4 text-muted-foreground" />
                              ) : (
                                <ChevronRight className="size-4 text-muted-foreground" />
                              )
                            ) : (
                              <span className="size-4" />
                            )}
                          </div>

                          {/* 时间 */}
                          <div className="w-[135px] shrink-0 py-2.5 text-xs text-muted-foreground">
                            {formatTimestamp(entry.created_at)}
                          </div>

                          {/* 智能体 */}
                          <div className="w-[155px] shrink-0 py-2.5 font-mono text-xs text-foreground/80">
                            {entry.agent_name}
                          </div>

                          {/* 用户 */}
                          <div className="w-[155px] shrink-0 truncate py-2.5 text-xs text-muted-foreground">
                            {entry.user_email ?? "—"}
                          </div>

                          {/* 提示词 */}
                          <div className="min-w-0 flex-1 truncate py-2.5 pr-3 text-xs text-muted-foreground">
                            {entry.input_summary ?? "—"}
                          </div>

                          {/* Token */}
                          <div className="w-[80px] shrink-0 py-2.5 text-right text-xs text-muted-foreground">
                            {formatNumber(entry.tokens_in + entry.tokens_out)}
                          </div>

                          {/* 耗时 */}
                          <div className="w-[75px] shrink-0 py-2.5 text-right text-xs text-muted-foreground">
                            {formatDuration(entry.duration_ms)}
                          </div>

                          {/* 状态 */}
                          <div className="w-[70px] shrink-0 py-2.5">
                            {entry.status === "success" ? (
                              <Badge
                                variant="outline"
                                className="border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-400"
                              >
                                成功
                              </Badge>
                            ) : (
                              <Badge
                                variant="outline"
                                className="border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-400"
                              >
                                失败
                              </Badge>
                            )}
                          </div>

                          {/* 追踪链接 */}
                          <div className="w-[56px] shrink-0 py-2.5 pr-3">
                            {entry.trace_id ? (
                              <a
                                href={`${JAEGER_BASE}/trace/${entry.trace_id}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                onClick={(e) => e.stopPropagation()}
                                title={`在 Jaeger 中查看追踪 ${entry.trace_id}`}
                                className="inline-flex items-center gap-0.5 text-xs text-primary hover:underline"
                              >
                                <ExternalLink className="size-3" />
                                Jaeger
                              </a>
                            ) : (
                              <span className="text-xs text-muted-foreground/40">—</span>
                            )}
                          </div>
                        </div>

                        {/* 展开的工具步骤 */}
                        {isExpanded && hasSteps && (
                          <div className="border-t border-border bg-muted/40 px-4 pb-3 pt-2">
                            <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                              <Wrench className="size-3" />
                              工具步骤（{entry.steps.length}）
                            </div>
                            <div className="space-y-1.5">
                              {entry.steps.map((step) => (
                                <StepDetail key={step.step_index} step={step} />
                              ))}
                            </div>
                            {entry.error_message && (
                              <p className="mt-2 rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">
                                {entry.error_message}
                              </p>
                            )}
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}

          {/* 分页 */}
          {!loading && total > PAGE_SIZE && (
            <>
              <Separator />
              <div className="flex items-center justify-between px-4 py-3">
                <span className="text-xs text-muted-foreground">
                  第 {page + 1} / {totalPages} 页 &middot;{" "}
                  共 {total.toLocaleString()} 条
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page === 0}
                    onClick={() => setPage((p) => p - 1)}
                  >
                    上一页
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page >= totalPages - 1}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
