"use client";

import { useEffect, useState } from "react";
import { api, type RunEntry } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Activity,
  ChevronDown,
  Check,
  X,
  Clock,
  Zap,
  ExternalLink,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";

type CheckpointData = Awaited<ReturnType<typeof api.getRunCheckpoints>>;

const HITL_STATUS_LABELS: Record<string, string> = {
  pending: "待审批",
  approved: "已批准",
  rejected: "已拒绝",
  denied: "已拒绝",
  expired: "已过期",
};

export default function RunsPage() {
  const [entries, setEntries] = useState<RunEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  // 以 run id 为键。对每个可见行都提前拉取，而不是展开时才懒加载 ——
  // 懒加载意味着下方的「待审批」徽章在行被打开过一次之前都无法显示，
  // 而它本来的用途正是让列表可被快速扫视。每次请求都是一次带索引的
  // 单点查询；本页是低频的管理界面，不是热点路径。
  const [checkpoints, setCheckpoints] = useState<Record<string, CheckpointData>>({});
  const limit = 20;

  useEffect(() => {
    setLoading(true);
    api
      .getRuns({ limit, offset: page * limit })
      .then((r) => {
        setEntries(r.entries);
        setTotal(r.total);
        setLoading(false);
        setCheckpoints({});
        Promise.allSettled(r.entries.map((e) => api.getRunCheckpoints(e.id))).then((results) => {
          setCheckpoints((prev) => {
            const next = { ...prev };
            results.forEach((res, i) => {
              if (res.status === "fulfilled") next[r.entries[i].id] = res.value;
            });
            return next;
          });
        });
      })
      .catch(() => {
        setError("运行记录加载失败");
        setLoading(false);
      });
  }, [page]);

  function refreshCheckpoints(runId: string) {
    api
      .getRunCheckpoints(runId)
      .then((data) => setCheckpoints((prev) => ({ ...prev, [runId]: data })))
      .catch(() => {});
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">智能体运行记录</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            近期「编排器 → 专业智能体 → 工具」执行链路
          </p>
        </div>
        {total > 0 && (
          <Badge variant="outline" className="text-xs">
            共 {total} 条
          </Badge>
        )}
      </div>

      {loading && (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-16 animate-pulse rounded-lg bg-muted/60" />
          ))}
        </div>
      )}

      {error && (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            {error}
          </CardContent>
        </Card>
      )}

      {!loading && !error && entries.length === 0 && (
        <Card>
          <CardContent className="py-16 text-center">
            <Activity className="mx-auto mb-3 size-8 text-muted-foreground/40" />
            <p className="font-medium text-foreground">暂无运行记录</p>
            <p className="mt-1 text-sm text-muted-foreground">
              在对话中发送消息，即可在此查看智能体执行链路。
            </p>
          </CardContent>
        </Card>
      )}

      {!loading && !error && entries.length > 0 && (
        <div className="space-y-2">
          {entries.map((entry) => (
            <RunRow
              key={entry.id}
              entry={entry}
              checkpointData={checkpoints[entry.id]}
              onResumed={() => refreshCheckpoints(entry.id)}
            />
          ))}
        </div>
      )}

      {total > limit && (
        <div className="flex items-center justify-center gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
          >
            上一页
          </Button>
          <span className="text-xs text-muted-foreground">
            第 {page + 1} / {Math.ceil(total / limit)} 页
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={(page + 1) * limit >= total}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </Button>
        </div>
      )}
    </div>
  );
}

function RunRow({
  entry,
  checkpointData,
  onResumed,
}: {
  entry: RunEntry;
  checkpointData: CheckpointData | undefined;
  onResumed: () => void;
}) {
  const [open, setOpen] = useState(false);
  const hasSteps = entry.steps.length > 0;

  // 工作流模式（workflow:pre-purchase、workflow:return-replace、group-chat）
  // 不会写入 agent_execution_steps 行 —— 只有 "tool" 模式调用
  // log_execution_step() 才会 —— 因此单看 hasSteps 会恰好对最可能
  // 存在待审批的运行记录隐藏展开入口。checkpointData 由父组件传入
  // （对每个可见行提前拉取，而非展开时懒加载 —— 原因见 RunsPage 注释）。
  const [resuming, setResuming] = useState<"approve" | "reject" | null>(null);
  const [resumeError, setResumeError] = useState<string | null>(null);

  const agentsInvolved = Array.from(
    new Set(entry.steps.map((s) => s.tool_name.split(":")[0]).filter(Boolean))
  );

  async function handleResume(approved: boolean) {
    setResuming(approved ? "approve" : "reject");
    setResumeError(null);
    try {
      await api.resumeRun(entry.id, approved);
      onResumed();
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : "恢复执行失败。");
    } finally {
      setResuming(null);
    }
  }

  const hitl = checkpointData?.hitl_request;
  const pendingApproval = hitl?.status === "pending";

  return (
    <Card className={cn("overflow-hidden", pendingApproval && "ring-1 ring-amber-400/60")}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-start gap-3 p-4 text-left hover:bg-muted/30 transition-colors"
      >
        <div className="mt-0.5 shrink-0">
          {entry.status === "success" ? (
            <Check className="size-4 text-emerald-500" />
          ) : (
            <X className="size-4 text-destructive" />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">
            {entry.input_summary ?? "—"}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Clock className="size-3" />
              {new Date(entry.created_at).toLocaleString()}
            </span>
            {entry.duration_ms > 0 && (
              <span className="flex items-center gap-1">
                <Zap className="size-3" />
                {entry.duration_ms} ms
              </span>
            )}
            {entry.tokens_in + entry.tokens_out > 0 && (
              <span>{entry.tokens_in + entry.tokens_out} Token</span>
            )}
            {agentsInvolved.length > 0 && (
              <span className="text-muted-foreground/60">
                {agentsInvolved.join(" → ")}
              </span>
            )}
            {entry.trace_id && (
              <a
                href={`http://localhost:16686`}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="flex items-center gap-0.5 text-primary hover:underline"
              >
                Jaeger <ExternalLink className="size-2.5" />
              </a>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {pendingApproval && (
            <Badge className="bg-amber-500 text-white text-xs">待审批</Badge>
          )}
          {hasSteps && (
            <Badge variant="outline" className="text-xs">
              {entry.steps.length} 步
            </Badge>
          )}
          <ChevronDown
            className={cn(
              "size-4 text-muted-foreground transition-transform",
              open && "rotate-180"
            )}
          />
        </div>
      </button>

      {open && (
        <div className="border-t bg-muted/10">
          {hasSteps && (
            <ol className="divide-y text-xs">
              {entry.steps.map((step, i) => (
                <StepDetailRow key={i} step={step} />
              ))}
            </ol>
          )}

          {checkpointData === undefined && (
            <p className="px-4 py-3 text-xs text-muted-foreground">正在加载检查点…</p>
          )}

          {hitl && (
            <div className="space-y-2 border-t px-4 py-3 text-xs">
              <p className="font-medium text-foreground">
                退货审批 —— {HITL_STATUS_LABELS[hitl.status] ?? hitl.status}
                {hitl.payload?.order_id ? `（订单 ${hitl.payload.order_id}）` : ""}
              </p>
              {pendingApproval ? (
                <>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      disabled={resuming !== null}
                      onClick={() => handleResume(true)}
                    >
                      {resuming === "approve" && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
                      批准
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={resuming !== null}
                      onClick={() => handleResume(false)}
                    >
                      {resuming === "reject" && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
                      拒绝
                    </Button>
                  </div>
                  {resumeError && <p className="text-destructive">{resumeError}</p>}
                </>
              ) : (
                <p className="text-muted-foreground">
                  已于 {hitl.responded_at ? new Date(hitl.responded_at).toLocaleString() : ""} 处理
                </p>
              )}
            </div>
          )}

          {!hasSteps && checkpointData !== undefined && !hitl && (
            <p className="px-4 py-3 text-xs text-muted-foreground">该运行记录没有更多详情。</p>
          )}
        </div>
      )}
    </Card>
  );
}

function StepDetailRow({ step }: { step: RunEntry["steps"][number] }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = step.tool_input || step.tool_output;

  // 数据库中 tool_name 以 "agent:tool" 形式存储（例如 "product-discovery:search_products"）
  const colonIdx = step.tool_name.indexOf(":");
  const agentLabel = colonIdx > 0 ? step.tool_name.slice(0, colonIdx) : "orchestrator";
  const toolLabel = colonIdx > 0 ? step.tool_name.slice(colonIdx + 1) : step.tool_name;

  return (
    <li>
      <button
        type="button"
        disabled={!hasDetail}
        onClick={() => setExpanded((e) => !e)}
        className={cn(
          "flex w-full items-center gap-2 px-4 py-2 text-left",
          hasDetail ? "cursor-pointer hover:bg-muted/40" : "cursor-default"
        )}
      >
        <span className="w-5 text-center text-muted-foreground/40">{step.step_index + 1}</span>
        {hasDetail ? (
          <ChevronDown
            className={cn(
              "size-3 shrink-0 text-muted-foreground/50 transition-transform",
              expanded && "rotate-180"
            )}
          />
        ) : (
          <span className="size-3 shrink-0" />
        )}
        <span className="rounded bg-primary/10 px-1.5 py-0.5 font-medium text-primary">
          {agentLabel}
        </span>
        <span className="truncate font-mono text-foreground/80">{toolLabel}</span>
        {step.status === "error" ? (
          <X className="size-3 shrink-0 text-destructive" />
        ) : (
          <Check className="size-3 shrink-0 text-emerald-500" />
        )}
        <span className="ml-auto shrink-0 text-muted-foreground">{step.duration_ms} ms</span>
      </button>

      {expanded && hasDetail && (
        <div className="space-y-2 border-t bg-muted/20 px-4 py-2">
          {step.tool_input && (
            <JsonBlock label="输入" value={step.tool_input} />
          )}
          {step.tool_output && (
            <JsonBlock label="输出" value={step.tool_output} />
          )}
        </div>
      )}
    </li>
  );
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  const text =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const truncated = text.length > 1200;
  const display = truncated ? text.slice(0, 1200) + "\n…" : text;

  return (
    <div>
      <p className="mb-0.5 text-xs font-medium text-muted-foreground">{label}</p>
      <pre className="max-h-48 overflow-auto rounded bg-background/60 p-2 text-[10px] leading-relaxed text-foreground/80 ring-1 ring-border/60">
        {display}
      </pre>
    </div>
  );
}
