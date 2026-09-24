"use client";

import { useState } from "react";
import { ChevronRight, Check, X, ChevronDown } from "lucide-react";
import type { AgentStep } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 可折叠的「智能体活动」时间线——渲染通过 `event: step` SSE 帧流式推送的
 * 工具调用步骤（编排器 → 专业智能体 → 工具）。
 * 每个步骤行都可展开，以格式化 JSON 展示 tool_input / tool_output。
 */
export function AgentTimeline({ steps }: { steps: AgentStep[] }) {
  // 默认展开——智能体时间线正是本仓库存在要展示的东西；
  // 把它藏在一次点击之后反而埋没了重点。
  const [open, setOpen] = useState(true);

  if (!steps.length) return null;

  return (
    <div className="mt-2 max-w-xl rounded-lg border bg-card/60 text-xs">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
        <span>
          智能体活动 · {steps.length} 步
        </span>
        <span className="ml-auto text-muted-foreground/60">
          共 {steps.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0)}ms
        </span>
      </button>

      {open && (
        <ol className="divide-y border-t">
          {steps.map((s, i) => (
            <StepRow key={i} step={s} />
          ))}
        </ol>
      )}
    </div>
  );
}

function StepRow({ step: s }: { step: AgentStep }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = s.tool_input !== undefined || s.tool_output !== undefined;

  return (
    <li>
      <button
        type="button"
        disabled={!hasDetail}
        onClick={() => setExpanded((e) => !e)}
        className={cn(
          "flex w-full items-center gap-2 px-2.5 py-1.5 text-left",
          hasDetail ? "cursor-pointer hover:bg-muted/40" : "cursor-default"
        )}
      >
        {hasDetail && (
          <ChevronDown
            className={cn(
              "size-3 shrink-0 text-muted-foreground/60 transition-transform",
              expanded && "rotate-180"
            )}
          />
        )}
        {!hasDetail && <span className="size-3 shrink-0" />}

        <span className="rounded bg-primary/10 px-1.5 py-0.5 font-medium text-primary">
          {s.agent ?? "orchestrator"}
        </span>
        <span className="truncate font-mono text-foreground/80">{s.tool_name}</span>
        {s.status === "error" ? (
          <X className="size-3 shrink-0 text-destructive" />
        ) : (
          <Check className="size-3 shrink-0 text-emerald-500" />
        )}
        {typeof s.duration_ms === "number" && (
          <span className="ml-auto shrink-0 text-muted-foreground">{s.duration_ms}ms</span>
        )}
      </button>

      {expanded && hasDetail && (
        <div className="space-y-1.5 border-t bg-muted/20 px-3 py-2">
          {s.tool_input !== undefined && (
            <JsonBlock label="输入" value={s.tool_input} />
          )}
          {s.tool_output !== undefined && (
            <JsonBlock label="输出" value={s.tool_output} />
          )}
          {s.provenance && s.provenance.row_ids.length > 0 && (
            <p className="text-muted-foreground">
              数据来源 <span className="font-mono text-foreground/80">{s.provenance.source}</span>
              {" — "}
              {s.provenance.row_ids.length} 行记录
              {": "}
              <span className="font-mono text-foreground/70">{s.provenance.row_ids.join(", ")}</span>
            </p>
          )}
        </div>
      )}
    </li>
  );
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  const text =
    typeof value === "string"
      ? value
      : JSON.stringify(value, null, 2);

  // 过长的输出做截断，便于阅读
  const truncated = text.length > 800;
  const display = truncated ? text.slice(0, 800) + "\n…" : text;

  return (
    <div>
      <p className="mb-0.5 font-medium text-muted-foreground">{label}</p>
      <pre className="max-h-40 overflow-auto rounded bg-background/60 p-2 text-[10px] leading-relaxed text-foreground/80 ring-1 ring-border/60">
        {display}
      </pre>
    </div>
  );
}
