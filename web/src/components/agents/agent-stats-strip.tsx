"use client";

import { Activity, Zap, Layers } from "lucide-react";
import { StatCard } from "@/components/ui/stat-card";

interface AgentStat {
  agent_name: string;
  request_count: number;
  avg_duration_ms: number;
  total_tokens: number;
}

interface AgentStatsStripProps {
  backendName: string;
  stats: AgentStat[] | null;
}

/** 耗时格式化：0 显示为占位符，不足 1 秒按毫秒，其余按秒保留一位小数。 */
function formatDuration(ms: number): string {
  if (ms === 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Token 数格式化：超过百万按 M、超过千按 k 缩写。 */
function formatTokens(n: number): string {
  if (n === 0) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return String(n);
}

/**
 * 智能体运行时统计条：调用次数、平均耗时、Token 消耗。
 *
 * `stats` 为 null 表示仍在加载，此时数字显示占位符而非 0；加载完成后确实为 0
 * 的项才显示 0。
 */
export function AgentStatsStrip({ backendName, stats }: AgentStatsStripProps) {
  const row = stats?.find((s) => s.agent_name === backendName);

  const invocations = row?.request_count ?? 0;
  const avgMs = row?.avg_duration_ms ?? 0;
  const tokens = row?.total_tokens ?? 0;

  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold text-foreground">
        最近 30 天
      </h2>
      <div className="grid gap-3 sm:grid-cols-3">
        <StatCard
          label="调用次数"
          value={invocations === 0 && stats !== null ? "0" : invocations === 0 ? "—" : invocations.toLocaleString()}
          icon={Activity}
          hint="累计请求数"
        />
        <StatCard
          label="平均耗时"
          value={formatDuration(avgMs)}
          icon={Zap}
          hint="端到端"
        />
        <StatCard
          label="Token 消耗"
          value={formatTokens(tokens)}
          icon={Layers}
          hint="输入 + 输出"
        />
      </div>
    </section>
  );
}
