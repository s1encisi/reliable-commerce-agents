"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  Activity,
  Zap,
  Bot,
  Clock,
  Loader2,
  ShieldAlert,
  LayoutDashboard,
} from "lucide-react";

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

interface AgentUsage {
  agent_id: string;
  agent_name: string;
  invocations: number;
  tokens_in: number;
  tokens_out: number;
  avg_duration_ms: number;
}

interface DailyTrend {
  date: string;
  invocations: number;
  tokens_in: number;
  tokens_out: number;
}

interface UsageStats {
  total_invocations: number;
  total_tokens_in: number;
  total_tokens_out: number;
  active_agents: number;
  pending_requests: number;
  avg_duration_ms: number;
  per_agent: AgentUsage[];
  daily_trend: DailyTrend[];
}

// ---------------------------------------------------------------------------
// 辅助函数
// ---------------------------------------------------------------------------

function formatNumber(n: number | undefined | null): string {
  if (n == null) return "0";
  if (n >= 100_000_000) return `${(n / 100_000_000).toFixed(1)} 亿`;
  if (n >= 10_000) return `${(n / 10_000).toFixed(1)} 万`;
  return n.toLocaleString("zh-CN");
}

function formatDuration(ms: number | undefined | null): string {
  if (ms == null) return "0 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function formatDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("zh-CN", {
      month: "short",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export default function AdminDashboardPage() {
  const router = useRouter();
  const { user, isAdmin, isLoading: authLoading } = useAuth();

  const [stats, setStats] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
    }
  }, [user, authLoading, router]);

  const loadStats = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const raw = await api.getUsageStats();
      // 将 API 响应映射为前端 UsageStats 结构
      const overall = raw.overall || {};
      setStats({
        total_invocations: overall.total_requests ?? 0,
        total_tokens_in: overall.total_tokens_in ?? 0,
        total_tokens_out: overall.total_tokens_out ?? 0,
        active_agents: raw.by_agent?.length ?? 0,
        pending_requests: overall.pending_requests ?? 0,
        avg_duration_ms: overall.avg_duration_ms ?? 0,
        per_agent: (raw.by_agent || []).map((a: Record<string, unknown>) => ({
          agent_id: a.agent_name,
          agent_name: a.agent_name as string,
          invocations: (a.request_count ?? 0) as number,
          tokens_in: (a.tokens_in ?? 0) as number,
          tokens_out: (a.tokens_out ?? 0) as number,
          avg_duration_ms: (a.avg_duration_ms ?? 0) as number,
        })),
        daily_trend: (raw.daily || []).map((d: Record<string, unknown>) => ({
          date: d.date as string,
          invocations: (d.request_count ?? 0) as number,
          tokens_in: (d.tokens_in ?? 0) as number,
          tokens_out: (d.tokens_out ?? 0) as number,
        })),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "统计数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user && isAdmin) loadStats();
  }, [user, isAdmin, loadStats]);

  if (authLoading) return null;

  // 非管理员拒绝访问
  if (!isAdmin) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="text-center">
          <div className="mx-auto flex size-16 items-center justify-center rounded-full bg-destructive/10">
            <ShieldAlert className="size-8 text-destructive" />
          </div>
          <h2 className="mt-4 text-lg font-semibold text-foreground">
            无权访问
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            您没有查看该页面的管理员权限。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      {/* 页头 */}
      <div className="border-b border-border bg-card">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
              <LayoutDashboard className="size-5 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-foreground">
                管理看板
              </h1>
              <p className="text-sm text-muted-foreground">
                平台概览与智能体用量指标
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* 内容区 */}
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-primary" />
            <span className="ml-2 text-sm text-muted-foreground">
              正在加载看板…
            </span>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {!loading && !error && stats && (
          <div className="space-y-8">
            {/* 概览卡片 */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    总调用次数
                  </CardTitle>
                  <Activity className="size-4 text-primary" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-foreground">
                    {formatNumber(stats.total_invocations)}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    总 Token 数
                  </CardTitle>
                  <Zap className="size-4 text-amber-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-foreground">
                    {formatNumber(stats.total_tokens_in + stats.total_tokens_out)}
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    输入 {formatNumber(stats.total_tokens_in)} /{" "}
                    输出 {formatNumber(stats.total_tokens_out)}
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    活跃智能体
                  </CardTitle>
                  <Bot className="size-4 text-sky-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-foreground">
                    {stats.active_agents}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    待处理请求
                  </CardTitle>
                  <Clock className="size-4 text-orange-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-foreground">
                    {stats.pending_requests}
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* 各智能体用量 */}
            <div className="rounded-xl bg-card ring-1 ring-foreground/10">
              <div className="px-4 py-3">
                <h2 className="text-sm font-semibold text-muted-foreground">
                  各智能体用量明细
                </h2>
              </div>
              <Separator />
              {stats.per_agent.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                  暂无智能体用量数据。
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>智能体</TableHead>
                      <TableHead className="text-right">调用次数</TableHead>
                      <TableHead className="text-right">输入 Token</TableHead>
                      <TableHead className="text-right">输出 Token</TableHead>
                      <TableHead className="text-right">平均耗时</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {stats.per_agent.map((agent) => (
                      <TableRow key={agent.agent_id}>
                        <TableCell className="font-medium text-foreground">
                          {agent.agent_name}
                        </TableCell>
                        <TableCell className="text-right">
                          {formatNumber(agent.invocations)}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {formatNumber(agent.tokens_in)}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {formatNumber(agent.tokens_out)}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {formatDuration(agent.avg_duration_ms)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </div>

            {/* 近 7 日趋势 */}
            <div className="rounded-xl bg-card ring-1 ring-foreground/10">
              <div className="px-4 py-3">
                <h2 className="text-sm font-semibold text-muted-foreground">
                  近 7 日趋势
                </h2>
              </div>
              <Separator />
              {stats.daily_trend.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                  暂无趋势数据。
                </div>
              ) : (
                <div className="divide-y divide-border">
                  {stats.daily_trend.map((day) => (
                    <div
                      key={day.date}
                      className="flex items-center justify-between px-4 py-3"
                    >
                      <span className="text-sm font-medium text-muted-foreground">
                        {formatDate(day.date)}
                      </span>
                      <div className="flex items-center gap-6 text-sm text-muted-foreground">
                        <span>
                          <span className="font-medium text-muted-foreground">
                            {formatNumber(day.invocations)}
                          </span>{" "}
                          次调用
                        </span>
                        <span>
                          <span className="font-medium text-muted-foreground">
                            {formatNumber(day.tokens_in + day.tokens_out)}
                          </span>{" "}
                          Token
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
