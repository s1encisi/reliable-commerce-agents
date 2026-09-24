"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Separator } from "@/components/ui/separator";
import { StatCard } from "@/components/ui/stat-card";
import { SectionHeader } from "@/components/ui/section-header";
import { ChartContainer, CHART_COLORS } from "@/components/ui/chart";
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
  ArrowDownToLine,
  ArrowUpFromLine,
  Clock,
  Loader2,
  ShieldAlert,
  BarChart3,
} from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";

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
      weekday: "short",
      month: "short",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

function shortDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("zh-CN", {
      month: "short",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

function InlineBar({
  value,
  max,
  className,
}: {
  value: number;
  max: number;
  className?: string;
}) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="h-1.5 w-full rounded-full bg-muted">
      <div
        className={`h-1.5 rounded-full transition-all ${className ?? "bg-primary"}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export default function AdminUsagePage() {
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
      setError(
        err instanceof Error ? err.message : "用量统计加载失败",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user && isAdmin) loadStats();
  }, [user, isAdmin, loadStats]);

  if (authLoading) return null;

  if (!isAdmin) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="text-center">
          <div className="mx-auto flex size-16 items-center justify-center rounded-full bg-destructive/10">
            <ShieldAlert className="size-8 text-destructive" />
          </div>
          <h2 className="mt-4 text-lg font-semibold">无权访问</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            您没有查看该页面的管理员权限。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      {/* 页头 */}
      <div className="mb-8 flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
          <BarChart3 className="size-5 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-2xl font-bold tracking-tight">用量分析</h1>
          <p className="text-sm text-muted-foreground">
            全部智能体的 Token 与调用次数明细指标
          </p>
        </div>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="size-6 animate-spin text-primary" />
          <span className="ml-2 text-sm text-muted-foreground">
            正在加载用量数据…
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
          {/* 汇总卡片 */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="总调用次数"
              value={formatNumber(stats.total_invocations)}
              icon={Activity}
              hint={`${stats.active_agents} 个活跃智能体`}
            />
            <StatCard
              label="输入 Token"
              value={formatNumber(stats.total_tokens_in)}
              icon={ArrowDownToLine}
            />
            <StatCard
              label="输出 Token"
              value={formatNumber(stats.total_tokens_out)}
              icon={ArrowUpFromLine}
            />
            <StatCard
              label="平均耗时"
              value={formatDuration(stats.avg_duration_ms)}
              icon={Clock}
            />
          </div>

          {/* 每日趋势图 */}
          <div className="rounded-xl bg-card p-4 ring-1 ring-foreground/10">
            <SectionHeader
              eyebrow="近 7 天"
              title="每日活跃度"
              description="每日智能体调用次数。"
            />
            {stats.daily_trend.length === 0 ? (
              <div className="py-12 text-center text-sm text-muted-foreground">
                暂无趋势数据。
              </div>
            ) : (
              <ChartContainer height={260}>
                <BarChart
                  data={stats.daily_trend.map((d) => ({
                    ...d,
                    label: shortDate(d.date),
                  }))}
                  margin={{ top: 8, right: 8, left: -16, bottom: 0 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="var(--border)"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="label"
                    tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <YAxis
                    tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
                    tickLine={false}
                    axisLine={false}
                    allowDecimals={false}
                  />
                  <Tooltip
                    cursor={{ fill: "var(--accent)", opacity: 0.4 }}
                    contentStyle={{
                      background: "var(--popover)",
                      border: "1px solid var(--border)",
                      borderRadius: "0.5rem",
                      color: "var(--popover-foreground)",
                      fontSize: "12px",
                    }}
                  />
                  <Bar
                    dataKey="invocations"
                    name="调用次数"
                    fill={CHART_COLORS[0]}
                    radius={[4, 4, 0, 0]}
                    maxBarSize={48}
                  />
                </BarChart>
              </ChartContainer>
            )}
          </div>

          {/* 各智能体明细 */}
          <div className="rounded-xl bg-card ring-1 ring-foreground/10">
            <div className="px-4 py-3">
              <h2 className="text-sm font-semibold">各智能体明细</h2>
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
                    <TableHead className="w-[120px]">占比</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(() => {
                    const maxInvocations = Math.max(
                      ...stats.per_agent.map((a) => a.invocations),
                      1,
                    );
                    return stats.per_agent.map((agent) => (
                      <TableRow key={agent.agent_id}>
                        <TableCell className="font-medium">
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
                        <TableCell>
                          <InlineBar
                            value={agent.invocations}
                            max={maxInvocations}
                          />
                        </TableCell>
                      </TableRow>
                    ));
                  })()}
                </TableBody>
              </Table>
            )}
          </div>

          {/* 每日明细表 */}
          <div className="rounded-xl bg-card ring-1 ring-foreground/10">
            <div className="px-4 py-3">
              <h2 className="text-sm font-semibold">近 7 日趋势</h2>
            </div>
            <Separator />
            {stats.daily_trend.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                暂无趋势数据。
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>日期</TableHead>
                    <TableHead className="text-right">调用次数</TableHead>
                    <TableHead className="text-right">输入 Token</TableHead>
                    <TableHead className="text-right">输出 Token</TableHead>
                    <TableHead className="text-right">Token 合计</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {stats.daily_trend.map((day) => (
                    <TableRow key={day.date}>
                      <TableCell className="font-medium">
                        {formatDate(day.date)}
                      </TableCell>
                      <TableCell className="text-right">
                        {formatNumber(day.invocations)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {formatNumber(day.tokens_in)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {formatNumber(day.tokens_out)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {formatNumber(day.tokens_in + day.tokens_out)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
