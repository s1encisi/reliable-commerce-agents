"use client";

import { useState } from "react";
import { CheckIcon, XIcon, Loader2Icon, ShieldAlertIcon } from "lucide-react";
import { api } from "@/lib/api";

/**
 * 针对暂停在人工确认环节的运行，提供批准/拒绝控件，内联渲染在对话流中。
 *
 * `workflow:return-replace` 会停在 `ctx.request_info` 上，并且确实就停在
 * 那里。在此之前，唯一能放行它的控件位于 `/runs` 页面，因此发起这次暂停
 * 的用户——就坐在对话里，看着一条退货退到一半戛然而止的消息——如果不
 * 知道另有一个页面、且自己的运行正躺在那个页面上，就无从操作。一个发起者
 * 看不到的暂停，与卡死无法区分。
 *
 * 结果处理刻意使用本地状态，而不是重新拉取。恢复后的文本由 resume 调用
 * 直接返回，因此对话流可以立刻展示结果；重新读取整个会话只会是获知「本
 * 组件已经被明确告知的事情」的一种更慢的方式。
 */

export type ApprovalOutcome = { approved: boolean; text: string; agentsInvolved: string[] };

export interface ApprovalCardProps {
  runId: string;
  /** 以恢复后的这一轮内容回调，供调用方追加到对话流中。 */
  onResolved: (outcome: ApprovalOutcome) => void;
}

export function ApprovalCard({ runId, onResolved }: ApprovalCardProps) {
  const [pending, setPending] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<boolean | null>(null);

  async function resolve(approved: boolean) {
    setPending(approved ? "approve" : "reject");
    setError(null);
    try {
      const res = await api.resumeRun(runId, approved);
      setDone(approved);
      onResolved({
        approved,
        text: res.text,
        agentsInvolved: res.agents_involved ?? [],
      });
    } catch (err) {
      // 把失败暴露出来，而不是悄悄退回两个可点的按钮：在一个已经处理过的
      // 审批上再点一次，正是这个控件绝不能引诱用户犯的错误。
      setError(err instanceof Error ? err.message : "无法提交该决定。");
    } finally {
      setPending(null);
    }
  }

  if (done !== null) {
    return (
      <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        {done ? <CheckIcon className="size-3.5" /> : <XIcon className="size-3.5" />}
        {done ? "已批准" : "已拒绝"} —— 工作流已从检查点继续执行。
      </div>
    );
  }

  return (
    <div className="mt-2.5 rounded-lg border border-amber-500/40 bg-amber-500/5 p-2.5">
      <div className="flex items-center gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-400">
        <ShieldAlertIcon className="size-3.5 shrink-0" />
        这笔退货需要您审批后才能继续
      </div>

      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => resolve(true)}
          disabled={pending !== null}
          className="inline-flex h-7 items-center gap-1 rounded-md bg-primary px-2.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
        >
          {pending === "approve" ? (
            <Loader2Icon className="size-3 animate-spin" />
          ) : (
            <CheckIcon className="size-3" />
          )}
          批准
        </button>
        <button
          type="button"
          onClick={() => resolve(false)}
          disabled={pending !== null}
          className="inline-flex h-7 items-center gap-1 rounded-md border px-2.5 text-xs font-medium transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
        >
          {pending === "reject" ? (
            <Loader2Icon className="size-3 animate-spin" />
          ) : (
            <XIcon className="size-3" />
          )}
          拒绝
        </button>
      </div>

      {error && <p className="mt-1.5 text-[11px] text-destructive">{error}</p>}
    </div>
  );
}
