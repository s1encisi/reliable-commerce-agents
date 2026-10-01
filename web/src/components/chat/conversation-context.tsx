"use client";

import { useRef, useState, type FormEvent } from "react";
import { api, ApiError, type ConversationContext } from "@/lib/api";
import { errorMessage } from "@/lib/business-copy";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/** 实例按 conversationId remount，避免把前一个会话的草稿带入新会话。 */
export function ConversationContextEditor({ conversationId, disabled = false }: { conversationId: string; disabled?: boolean }) {
  const [snapshot, setSnapshot] = useState<ConversationContext | null>(null);
  const [goal, setGoal] = useState("");
  const [constraints, setConstraints] = useState("");
  const [goalEdited, setGoalEdited] = useState(false);
  const [constraintsEdited, setConstraintsEdited] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const lock = useRef(false);

  async function load(keepDraft = false) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try {
      const next = await api.getConversationContext(conversationId);
      setSnapshot(next); setConflict(false);
      if (!keepDraft) { setGoal(next.context.goal ?? ""); setConstraints((next.context.constraints ?? []).join("\n")); setGoalEdited(false); setConstraintsEdited(false); }
      setNotice(keepDraft ? "已载入最新版本并保留您的草稿。请对照当前服务端内容，确认后再保存；仅提交您编辑的字段。" : "");
    } catch (err) { setError(errorMessage(err, "会话约束读取失败，请重试。")); }
    finally { lock.current = false; setBusy(false); }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!snapshot || conflict || disabled || lock.current) return;
    const lines = constraints.split("\n").map((line) => line.trim()).filter(Boolean);
    if (lines.length > 30) { setError("约束不超过 30 行，请合并后重试。"); return; }
    const changed: { goal?: string; constraints?: string[] } = {};
    if (goalEdited && goal.trim() !== (snapshot.context.goal ?? "")) changed.goal = goal.trim();
    if (constraintsEdited && JSON.stringify(lines) !== JSON.stringify(snapshot.context.constraints ?? [])) changed.constraints = lines;
    if (!Object.keys(changed).length) { setNotice("没有需要保存的修改。"); return; }
    lock.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const next = await api.updateConversationContext(conversationId, snapshot.revision, changed);
      setSnapshot(next); setGoal(next.context.goal ?? ""); setConstraints((next.context.constraints ?? []).join("\n"));
      setGoalEdited(false); setConstraintsEdited(false);
      setNotice("目标与约束已保存，将用于后续对话。删除的约束已撤销。");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConflict(true); setError("其他请求已更新会话。您的草稿已保留，请先重新载入最新版本；不会自动重试保存。");
      } else setError(errorMessage(err, "保存结果尚未确认，请重新载入后核实。"));
    } finally { lock.current = false; setBusy(false); }
  }

  return <details className="shrink-0 border-b px-3 py-2" onToggle={(event) => { if (event.currentTarget.open && !snapshot && !busy) void load(); }}>
    <summary className="cursor-pointer text-sm font-medium">目标与约束</summary>
    <form onSubmit={save} className="mt-3 flex max-h-80 flex-col gap-3 overflow-y-auto pb-2">
      <p className="text-xs text-muted-foreground">修改目标会撤销旧约束与旧证据，请重新填写适用约束。其他情况删除某行即可撤销该约束。</p>
      {snapshot?.context.goal_needs_input && <p role="status" className="text-sm text-muted-foreground">原目标已随约束撤销，请明确填写新目标后再继续对话。</p>}
      {snapshot && <p className="text-xs text-muted-foreground">当前服务端版本 {snapshot.revision} · 目标：{snapshot.context.goal || "未设置"} · 约束：{snapshot.context.constraints?.join("；") || "无"}</p>}
      <div className="flex flex-col gap-1"><Label htmlFor={`context-goal-${conversationId}`}>当前目标</Label><Input id={`context-goal-${conversationId}`} value={goal} maxLength={6000} disabled={disabled || busy || !snapshot} onChange={(event) => { if (goal === (snapshot?.context.goal ?? "") && event.target.value !== goal) { setConstraints(""); setConstraintsEdited(true); } setGoalEdited(true); setGoal(event.target.value); }} /></div>
      <div className="flex flex-col gap-1"><Label htmlFor={`context-constraints-${conversationId}`}>当前约束（每行一条）</Label><Textarea id={`context-constraints-${conversationId}`} value={constraints} disabled={disabled || busy || !snapshot} onChange={(event) => { setConstraintsEdited(true); setConstraints(event.target.value); }} /></div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
      <div className="flex flex-wrap gap-2"><Button type="submit" size="sm" disabled={disabled || busy || conflict || !snapshot}>保存目标与约束</Button><Button type="button" size="sm" variant="outline" disabled={busy || disabled} onClick={() => load(!!snapshot)}>重新载入{snapshot ? "（保留草稿）" : ""}</Button></div>
    </form>
  </details>;
}
