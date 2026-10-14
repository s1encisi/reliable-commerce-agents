"use client";

import { Suspense, useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, Pause, Play, RefreshCw, ShoppingBag } from "lucide-react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { errorMessage } from "@/lib/business-copy";
import { formatDateTime, formatPrice } from "@/lib/format";
import { ACTIVE_STATUSES, CAPABILITY_LABELS, EDITABLE_STATUSES, PROCUREMENT_STATUS, STEP_STATUS, STOP_REASONS, buildProcurementSpec, planLayers, procurementApi, procurementGoal, reconcileProcurement, streamProcurement, type ProcurementCapabilities, type ProcurementOption, type ProcurementSpec, type ProcurementTask } from "@/lib/procurement";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const money = (value: string) => formatPrice(Number(value));

export default function ProcurementPage() {
  return <Suspense fallback={<div className="p-6"><Skeleton className="h-40 w-full" /></div>}><ProcurementWorkspace /></Suspense>;
}

export function ProcurementWorkspace() {
  const { user } = useAuth();
  const owner = user?.email;
  const router = useRouter();
  const selectedId = useSearchParams().get("task");
  const [task, setTask] = useState<ProcurementTask | null>(null);
  const taskRef = useRef<ProcurementTask | null>(null);
  const generation = useRef(0);
  const mutation = useRef(false);
  const [capability, setCapability] = useState<ProcurementCapabilities | null>(null);
  const [goal, setGoal] = useState("");
  const [lookup, setLookup] = useState("");
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [connection, setConnection] = useState("未连接");
  const [streamAttempt, setStreamAttempt] = useState(0);
  const cursor = useRef({ id: "", seq: 0 });
  const busy = Boolean(pending);
  const allowed = capability?.enabled && (capability.mode === "offline" || capability.model_authorized);
  const taskAllowed = allowed && (task?.execution_mode !== "model" || capability?.model_authorized);

  const accept = useCallback((incoming: ProcurementTask, epoch: number) => {
    if (epoch !== generation.current) return;
    if (incoming.task_type !== "procurement") { setError("此记录不是采购任务，请到任务工作台查看。"); return; }
    const next = reconcileProcurement(taskRef.current, incoming);
    taskRef.current = next; setTask(next);
    if (owner) {
      try { localStorage.setItem(`procurement:v1:${owner}`, next.id); }
      catch { setNotice("浏览器无法保存入口，请保留当前任务链接。"); }
    }
  }, [owner]);

  useEffect(() => {
    let cancelled = false;
    setCapability(null);
    if (owner) procurementApi.capabilities().then((value) => {
      if (!cancelled) setCapability(value.procurement ?? null);
    }).catch((err) => { if (!cancelled) setError(errorMessage(err, "无法确认采购服务能力，请稍后刷新页面。")); });
    return () => { cancelled = true; };
  }, [owner]);

  useEffect(() => {
    if (!owner) return;
    if (!selectedId) {
      try { const saved = localStorage.getItem(`procurement:v1:${owner}`); if (saved && UUID.test(saved)) router.replace(`/procurement?task=${saved}`); }
      catch { setNotice("无法读取最近任务，请用任务链接恢复。"); }
      return;
    }
    if (taskRef.current?.id === selectedId) return;
    const epoch = ++generation.current;
    taskRef.current = null; setTask(null); setError("");
    if (!UUID.test(selectedId)) { setError("任务标识格式不正确。"); return; }
    procurementApi.get(selectedId).then((data) => { accept(data, epoch); if (generation.current === epoch) setGoal(data.goal); })
      .catch((err) => { if (generation.current === epoch) setError(errorMessage(err, "无法读取采购任务。")); });
  }, [selectedId, owner, router, accept]);

  // Switching users invalidates all in-flight snapshots and their SSE reader.
  useEffect(() => () => { generation.current += 1; taskRef.current = null; }, [owner]);

  const refresh = useCallback(async () => {
    const current = taskRef.current, epoch = generation.current;
    if (!current) return;
    accept(await procurementApi.get(current.id), epoch);
  }, [accept]);

  const id = task?.id;
  const isActive = Boolean(task && ACTIVE_STATUSES.has(task.status));
  useEffect(() => {
    if (!id) return;
    const controller = new AbortController(), epoch = generation.current;
    if (cursor.current.id !== id) cursor.current = { id, seq: 0 };
    let reading = false, dirty = false;
    const reload = async () => {
      dirty = true;
      if (reading) return;
      reading = true;
      try {
        while (dirty && !controller.signal.aborted) {
          dirty = false;
          accept(await procurementApi.get(id), epoch);
        }
      } catch (err) { if (!controller.signal.aborted) setNotice(errorMessage(err, "事件已收到，但状态读取失败，请手动刷新。")); }
      finally { reading = false; }
    };
    setConnection("正在连接");
    streamProcurement(id, cursor.current.seq, controller.signal, (event) => {
      if (controller.signal.aborted || epoch !== generation.current) return;
      cursor.current.seq = event.seq;
      setConnection("实时连接中");
      void reload(); // Event step.status is not a task status; fetch the authoritative snapshot.
    }).then(() => {
      if (!controller.signal.aborted) { setConnection("连接已结束，可重新连接或手动刷新"); void reload(); }
    }).catch((err) => {
      if (!controller.signal.aborted) { setConnection("连接已断开"); setNotice(errorMessage(err, "实时连接中断，任务仍在服务端保存；重新连接会续接事件。")); }
    });
    return () => controller.abort();
  }, [id, isActive, streamAttempt, accept]);

  async function act(label: string, action: (epoch: number) => Promise<void>) {
    if (mutation.current) return;
    mutation.current = true; setPending(label); setError(""); setNotice("");
    const epoch = generation.current;
    try { await action(epoch); }
    catch (err) {
      if (generation.current !== epoch) return;
      setError(errorMessage(err));
      if (taskRef.current) {
        try { await refresh(); setNotice(err instanceof ApiError && err.status === 409 ? "已读取最新版本；本次操作没有自动重试，请重新核对。" : "已查询当前状态；未自动重放操作，请核对后继续。"); }
        catch { setNotice("状态暂时无法读取，请手动刷新后再操作。"); }
      }
    } finally { mutation.current = false; setPending(""); }
  }

  function parse(event: FormEvent) {
    event.preventDefault();
    if (!allowed || !goal.trim()) return;
    if (mutation.current) return;
    // Starting a different goal invalidates old task responses, but does not replay the previous task.
    generation.current += 1;
    void act("解析需求", async (epoch) => {
      const next = await procurementApi.parse(goal.trim());
      if (generation.current !== epoch) return;
      taskRef.current = null; accept(next, epoch); router.replace(`/procurement?task=${next.id}`);
    });
  }

  function confirmAndStart(spec: ProcurementSpec) {
    void act("确认并启动", async (epoch) => {
      let current = taskRef.current;
      if (!current || !taskAllowed) return;
      // PATCH keeps the human-readable goal consistent with the reviewed structured draft.
      current = await procurementApi.update(current.id, current.revision, procurementGoal(spec), spec);
      accept(current, epoch);
      if (epoch !== generation.current) return;
      current = await procurementApi.confirm(current.id, current.revision, spec);
      accept(current, epoch);
      if (epoch !== generation.current) return;
      const started = await procurementApi.start(current.id, current.revision);
      accept(started, epoch);
    });
  }

  return <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 md:p-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-2xl font-semibold">采购决策</h1><p className="mt-1 text-sm text-muted-foreground">为同一种商品比较独立采购方案，核对货款、可售数量和评论依据。</p></div><Badge variant="secondary">只读决策 · 不下单</Badge></div>
    <div className="rounded-lg border bg-muted/40 p-4 text-sm" role="status">
      {capability?.mode === "offline" ? "当前为离线模式：规则解析 / 固定离线计划 / 真实数据库工具，不调用模型。" : capability?.mode === "model" ? "当前为模型模式：模型提出草案和计划，执行结果需经数据库事实核验。" : "正在确认采购服务能力。"}
      {capability && !capability.enabled && <p className="mt-2 text-destructive">当前部署未启用采购服务。</p>}
      {capability?.mode === "model" && !capability.model_authorized && <p className="mt-2 text-destructive">尚未授权模型调用预算，解析和启动暂不可用。</p>}
    </div>
    {error && <p role="alert" className="rounded-lg border border-destructive/30 p-3 text-sm text-destructive">{error}</p>}
    {notice && <p role="status" className="rounded-lg border p-3 text-sm text-muted-foreground">{notice}</p>}
    <Card><CardHeader><CardTitle>1. 描述采购需求</CardTitle><CardDescription>例如：给小团队买 3 副办公耳机，总货款预算 1500 元，必须现货，提供 2 个备选。解析后仍需你确认。</CardDescription></CardHeader><CardContent className="flex flex-col gap-4">
      <form onSubmit={parse} className="flex flex-col gap-3"><Label htmlFor="procurement-goal">采购需求</Label><Textarea id="procurement-goal" value={goal} onChange={(event) => setGoal(event.target.value)} rows={3} maxLength={2000} placeholder="写明商品、数量和总货款预算" /><div className="flex flex-wrap gap-2"><Button type="submit" disabled={busy || !allowed || !goal.trim()}><ShoppingBag data-icon="inline-start" />解析为需求草案</Button>{capability?.mode === "offline" && <Button type="button" variant="outline" disabled={busy} onClick={() => setGoal("采购 3 副采购专用耳机，总预算 1500 元，必须现货，提供 2 个备选方案，评分至少 4。")}>填入本地合成示例</Button>}</div></form>
      <details className="text-sm"><summary className="cursor-pointer text-muted-foreground">通过任务标识恢复</summary><form className="mt-3 flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); if (UUID.test(lookup.trim())) router.replace(`/procurement?task=${lookup.trim()}`); else setError("任务标识格式不正确。"); }}><Label htmlFor="procurement-lookup" className="sr-only">采购任务标识</Label><Input id="procurement-lookup" className="min-w-0 flex-1" value={lookup} onChange={(event) => setLookup(event.target.value)} placeholder="任务 UUID" /><Button type="submit" variant="outline" disabled={busy}>打开任务</Button></form></details>
    </CardContent></Card>
    {task && <>
      <Card><CardHeader><div className="flex flex-wrap items-center justify-between gap-3"><CardTitle>当前任务 · {PROCUREMENT_STATUS[task.status] ?? "状态待核实"}</CardTitle><Badge variant="outline">需求 v{task.spec_revision} · 计划 v{task.plan_revision}</Badge></div><CardDescription className="break-all">任务 {task.id} · 控制版本 {task.revision} · 状态序号 {task.state_seq}</CardDescription></CardHeader><CardContent className="flex flex-col gap-3">
        <p className="text-sm">{task.goal}</p><p className="text-xs text-muted-foreground">任务执行模式：{task.execution_mode === "offline" ? "离线固定计划，不调用模型" : "模型规划，需有效调用授权"}</p><p className="text-sm text-muted-foreground">{connection} · 已收到事件 {cursor.current.seq}</p>
        <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={busy} onClick={() => void act("刷新", async () => { await refresh(); })}><RefreshCw data-icon="inline-start" />刷新状态</Button><Button variant="outline" disabled={busy} onClick={() => setStreamAttempt((value) => value + 1)}>重新连接事件</Button>
          {ACTIVE_STATUSES.has(task.status) && <Button variant="outline" disabled={busy || task.status === "pausing"} onClick={() => void act("暂停", async (epoch) => { const current = taskRef.current; if (current) accept(await procurementApi.pause(current.id, current.revision), epoch); })}><Pause data-icon="inline-start" />{task.status === "pausing" ? "等待当前执行暂停" : "暂停任务"}</Button>}
          {["pending", "completed", "needs_review", "failed", "budget_exhausted"].includes(task.status) && <Button variant="outline" disabled={busy} onClick={() => void act("转入需求修订", async (epoch) => { const current = taskRef.current; if (current) accept(await procurementApi.pause(current.id, current.revision), epoch); })}>修订需求</Button>}
          {task.status === "pending" && <Button disabled={busy || !taskAllowed} onClick={() => void act("启动", async (epoch) => { const current = taskRef.current; if (current) accept(await procurementApi.start(current.id, current.revision), epoch); })}><Play data-icon="inline-start" />启动已确认任务</Button>}
        </div>
        {pending && <p role="status" className="flex items-center gap-2 text-sm"><Loader2 className="size-4 animate-spin" />{pending}中…</p>}
        {task.stop_reason && <p className="rounded-md bg-muted p-3 text-sm">{STOP_REASONS[task.stop_reason] ?? (/[\u3400-\u9fff]/.test(task.stop_reason) ? task.stop_reason : "执行已停止，请查看步骤证据与诊断，核实后再处理。")}</p>}
        <p className="text-xs text-muted-foreground">模型调用 {task.counters.model_calls} · 规划 {task.counters.planning_attempts} · 修复 {task.counters.repairs_used} · 步骤尝试 {task.counters.step_attempts} · 复用证据 {task.counters.reused_steps}</p>
      </CardContent></Card>
      {EDITABLE_STATUSES.has(task.status) && <SpecEditor key={`${task.id}:${task.revision}`} task={task} busy={busy} allowed={Boolean(taskAllowed)} onConfirm={confirmAndStart} />}
      <PlanView task={task} />
      <RepairHistory task={task} />
      <section aria-label="采购备选方案" className="flex flex-col gap-4"><div><h2 className="text-lg font-semibold">采购备选方案</h2><p className="text-sm text-muted-foreground">每个方案都是单独购买一种商品 × 确认数量；两方案不合并计算。货款不含运费和税费。</p></div>
        {!task.options.length ? <p className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">尚未取得可交付方案，请查看执行状态和证据；这不表示采购成功。</p> : <div className="grid gap-4 lg:grid-cols-2">{task.options.map((option, index) => <OptionView key={option.product_id} option={option} index={index} />)}</div>}
      </section>
      <EvidenceView task={task} />
      <details className="rounded-lg border p-4 text-sm"><summary className="cursor-pointer">协议诊断（原始状态与版本）</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(task, null, 2)}</pre></details>
    </>}
  </div>;
}

function SpecEditor({ task, busy, allowed, onConfirm }: { task: ProcurementTask; busy: boolean; allowed: boolean; onConfirm: (spec: ProcurementSpec) => void }) {
  const draft = task.draft_spec ?? task.requirements;
  const [values, setValues] = useState({ query: draft?.query ?? "", quantity: draft?.quantity?.toString() ?? "", alternatives: draft?.alternative_count?.toString() ?? "2", budget: draft?.total_budget ?? "", rating: draft?.min_rating?.toString() ?? "", preferences: draft?.preferences?.join("\n") ?? "" });
  const [checked, setChecked] = useState(false);
  const update = (key: keyof typeof values, value: string) => { setValues((current) => ({ ...current, [key]: value })); setChecked(false); };
  const spec = buildProcurementSpec(values);
  const unsupported = task.draft_spec?._parse?.unsupported_conditions ?? [];
  const questions = task.draft_spec?._parse?.questions ?? [];
  return <Card><CardHeader><CardTitle>2. 核对需求并确认执行</CardTitle><CardDescription>{task.status === "paused" ? "任务已暂停。修改预算等条件后，重新核对并启动；服务端按新需求决定证据复用。" : "草案不是已确认的采购指令。缺失数量或预算时，请补充后再启动。"}</CardDescription></CardHeader><CardContent className="flex flex-col gap-4">
    {questions.length > 0 && <p className="text-sm text-muted-foreground">需要核对：{questions.join("；")}</p>}
    {unsupported.length > 0 && <div role="alert" className="rounded-md border border-destructive/30 p-3 text-sm text-destructive">当前范围无法执行：{unsupported.join("；")}。请修改上方原始需求后重新解析，不能跳过这些条件直接确认。</div>}
    <div className="grid gap-4 sm:grid-cols-2"><div className="flex flex-col gap-2 sm:col-span-2"><Label htmlFor="spec-query">商品检索词</Label><Input id="spec-query" value={values.query} onChange={(event) => update("query", event.target.value)} maxLength={2000} /></div>
      <div className="flex flex-col gap-2"><Label htmlFor="spec-quantity">每个方案采购数量</Label><Input id="spec-quantity" type="number" min={1} max={100} step={1} value={values.quantity} onChange={(event) => update("quantity", event.target.value)} /></div>
      <div className="flex flex-col gap-2"><Label htmlFor="spec-budget">每个方案总货款预算（元）</Label><Input id="spec-budget" inputMode="decimal" value={values.budget} onChange={(event) => update("budget", event.target.value)} /></div>
      <div className="flex flex-col gap-2"><Label htmlFor="spec-alternatives">独立备选方案数</Label><Input id="spec-alternatives" type="number" min={1} max={2} step={1} value={values.alternatives} onChange={(event) => update("alternatives", event.target.value)} /></div>
      <div className="flex flex-col gap-2"><Label htmlFor="spec-rating">最低商品评分（可选，0–5）</Label><Input id="spec-rating" type="number" min={0} max={5} step={0.1} value={values.rating} onChange={(event) => update("rating", event.target.value)} /></div>
      <div className="flex flex-col gap-2 sm:col-span-2"><Label htmlFor="spec-preferences">偏好（每行一项，需结合原文人工判断）</Label><Textarea id="spec-preferences" value={values.preferences} onChange={(event) => update("preferences", event.target.value)} /></div>
    </div>
    <p className="rounded-md bg-muted p-3 text-sm">固定范围：人民币 CNY · 必须现货 · 最多使用一张符合条件的优惠券 · 仅商品货款，不含运费和税费。报价不占券、不锁库存、不下单。</p>
    <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1 size-4 accent-primary" checked={checked} onChange={(event) => setChecked(event.target.checked)} />我已核对商品、数量、预算与以上范围，同意按此需求执行查询。</label>
    {!spec && <p className="text-sm text-muted-foreground">请填写商品、1–100 的整数数量、1–2 个备选及大于零且最多两位小数的预算。</p>}
    <div><Button disabled={busy || !allowed || !spec || !checked || unsupported.length > 0} onClick={() => spec && onConfirm(spec)}><Play data-icon="inline-start" />确认并自动启动</Button></div>
  </CardContent></Card>;
}

function PlanView({ task }: { task: ProcurementTask }) {
  return <Card><CardHeader><CardTitle>计划与执行</CardTitle><CardDescription>{task.plan?.source === "deterministic_demo" ? "固定离线计划 · 跨专业服务查询真实数据库" : task.plan?.source === "model" ? "模型规划 · 按依赖分层执行并核验" : "启动后生成计划；当前没有执行结果。"}</CardDescription></CardHeader><CardContent className="flex flex-col gap-4">
    {task.plan?.rationale && <p className="text-sm">{task.plan.rationale}</p>}
    {planLayers(task.plan?.steps ?? []).map((layer, index) => <section key={index} className="flex flex-col gap-2"><h3 className="text-sm font-medium">第 {index + 1} 层 · {layer.length > 1 ? "无依赖冲突的步骤可并行" : "按依赖执行"}</h3><div className="grid gap-3 md:grid-cols-2">{layer.map((node) => {
      const step = task.steps.find((value) => value.id === node.id && value.plan_revision === task.plan_revision);
      return <div key={node.id} className="flex flex-col gap-2 rounded-lg border p-3"><div className="flex flex-wrap justify-between gap-2"><span className="font-medium">{CAPABILITY_LABELS[node.capability] ?? "专业能力查询"}</span><Badge variant="secondary">{STEP_STATUS[step?.status ?? "pending"] ?? "状态待核实"}</Badge></div><p className="text-xs text-muted-foreground">节点 {node.id} · 依赖 {node.depends_on.join("、") || "无"}{step?.reused_from ? " · 复用已有证据" : ""}</p>{node.instruction && <p className="text-sm">{node.instruction}</p>}{step?.outcome?.message && <p className="text-sm">{step.outcome.message}</p>}{step?.outcome && <p className="text-xs text-muted-foreground">来源：{step.outcome.source} · 尝试 {step.attempt ?? 1}</p>}</div>;
    })}</div></section>)}
  </CardContent></Card>;
}

function RepairHistory({ task }: { task: ProcurementTask }) {
  const repairs = (task.receipt_history ?? []).filter((item) => item.kind === "repair_requested");
  if (!repairs.length) return null;
  return <Card><CardHeader><CardTitle>失败与局部修复记录</CardTitle><CardDescription>保留修复前的原因与受影响商品，是否已修复以当前计划和最终核验为准。</CardDescription></CardHeader><CardContent className="flex flex-col gap-3">{repairs.map((item, index) => <div className="rounded-lg border p-3 text-sm" key={`${item.plan_revision}:${index}`}><p className="font-medium">计划 v{item.plan_revision} · 发起修复</p>{item.failures?.map((failure, position) => <div className="mt-2 flex flex-col gap-1" key={position}><p>{failure.reason || "核验未通过，详见原始诊断。"}</p><p className="break-all text-xs text-muted-foreground">节点 {failure.step_id || "最终事实核验"}{failure.affected_product_ids?.length ? ` · 受影响商品 ${failure.affected_product_ids.join("、")}` : ""}</p></div>)}</div>)}</CardContent></Card>;
}

function OptionView({ option, index }: { option: ProcurementOption; index: number }) {
  return <Card><CardHeader><CardTitle>方案 {index + 1} · {option.name || "候选商品"}</CardTitle><CardDescription className="break-all">商品 {option.product_id} · 单一商品 × {option.quantity}</CardDescription></CardHeader><CardContent className="flex flex-col gap-4 text-sm">
    <dl className="grid grid-cols-2 gap-2"><dt className="text-muted-foreground">单价</dt><dd>{money(option.quote.unit_price)}</dd><dt className="text-muted-foreground">优惠前货款</dt><dd>{money(option.quote.original_total)}</dd><dt className="text-muted-foreground">单券优惠</dt><dd>{money(option.quote.discount_amount)}</dd><dt className="font-medium">最终货款</dt><dd className="font-semibold">{money(option.quote.final_total)}</dd><dt className="text-muted-foreground">可售 / 需求数量</dt><dd>{option.inventory.available_quantity} / {option.inventory.requested_quantity} · {option.inventory.sufficient ? "本次核验充足" : "库存不足"}</dd></dl>
    <p className="text-xs text-muted-foreground">{option.quote.coupon_id ? `优惠券依据：${option.quote.coupon_id}` : "本方案未使用优惠券"}。报价时有效，未占用券额度或锁定库存。</p>
    <div className="flex flex-col gap-2"><h3 className="font-medium">评论依据与风险</h3>{option.review.evidence_status !== "available" || !option.review.reviews.length ? <p className="text-destructive">评论证据不足，不能据此判断商品口碑或偏好满足度。</p> : <><p className="text-muted-foreground">以下为查询到的评论原文，评分和标记不等于已满足所有偏好。</p>{option.review.reviews.map((review) => <blockquote key={review.id} className="flex flex-col gap-1 border-l-2 pl-3"><p>{review.title} · {review.rating} 分</p><p className="whitespace-pre-wrap">{review.body}</p><cite className="break-all text-xs not-italic text-muted-foreground">评论引用 {review.id}</cite></blockquote>)}</>}
      {option.review.risk_references.map((risk) => <p key={`${risk.review_id}:${risk.reason}`} className="rounded-md bg-muted p-2">{risk.reason === "low_rating" ? "低评分评论" : risk.reason === "flagged_review" ? "存在标记的评论" : "待核实风险"}（{risk.review_id}）：{risk.quote}</p>)}
    </div>
  </CardContent></Card>;
}

function EvidenceView({ task }: { task: ProcurementTask }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const next = task.artifacts.map((item) => Date.parse(item.valid_until)).filter((time) => time > now).sort((a, b) => a - b)[0];
    if (!next) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.min(2_147_483_647, Math.max(0, next - Date.now() + 50)));
    return () => clearTimeout(timer);
  }, [task.artifacts, now]);
  return <Card><CardHeader><CardTitle>证据回执</CardTitle><CardDescription>保留失败与旧计划证据供核对。过期证据不能证明当前库存和报价。</CardDescription></CardHeader><CardContent className="flex flex-col gap-3">{!task.artifacts.length && <p className="text-sm text-muted-foreground">暂未收到证据。</p>}{task.artifacts.map((artifact) => <details key={artifact.artifact_id} className="rounded-lg border p-3 text-sm"><summary className="cursor-pointer">{CAPABILITY_LABELS[artifact.capability] ?? "查询证据"} · 计划 v{artifact.plan_revision}{artifact.plan_revision !== task.plan_revision ? "（历史计划）" : ""} · {Date.parse(artifact.valid_until) <= now ? "证据已过期" : "证据在有效期内"}</summary><p className="mt-2 text-xs text-muted-foreground">节点 {artifact.step_id} · {artifact.source} · 采集 {formatDateTime(artifact.observed_at)} · 有效至 {formatDateTime(artifact.valid_until)}</p><pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(artifact.data, null, 2)}</pre></details>)}</CardContent></Card>;
}
