"use client";

import { Suspense, useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ListChecks, Loader2, RefreshCw } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatPrice } from "@/lib/format";
import { errorMessage, taskScopeLabel, taskErrorLabel } from "@/lib/business-copy";
import { SERVICE_LABELS, TASK_STATUS_LABELS, type TaskInput, type TaskRecord, type TaskReceipt } from "@/lib/tasks";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";

const ACCEPTANCE_LABELS: Record<string, string> = {
  product_selection: "商品条件核验", product_comparison: "商品对比核验", inventory_check: "库存核验", order_lookup: "订单归属核验",
};
const STOCK_CHOICES = [{ label: "不限库存", value: "any" }, { label: "必须有库存", value: "available" }, { label: "只看缺货商品", value: "unavailable" }];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default function TasksPage() {
  return <Suspense fallback={<div className="p-6"><Skeleton className="h-40 w-full" /></div>}><TaskWorkspace /></Suspense>;
}

export function TaskWorkspace() {
  const { user } = useAuth();
  const owner = user?.email;
  const params = useSearchParams();
  const router = useRouter();
  const selectedId = params.get("task");
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [demoGoal, setDemoGoal] = useState<string | null>(null);
  const [lookup, setLookup] = useState("");
  const mutation = useRef(false);
  const [clock, setClock] = useState(() => Date.now() / 1000);
  // 只在下一个回执到期时更新展示，不轮询接口，也不自动执行查询。
  useEffect(() => {
    const expiries = Object.values(task?.results ?? {}).map((receipt) => receipt.valid_until).filter((expiry) => expiry > clock);
    if (!expiries.length) return;
    const delay = Math.min(2_147_483_647, Math.max(0, Math.min(...expiries) * 1000 - Date.now() + 25));
    const timer = setTimeout(() => setClock(Date.now() / 1000), delay);
    return () => clearTimeout(timer);
  }, [task, clock]);

  useEffect(() => {
    if (!owner) return;
    let cancelled = false;
    api.getRuntimeCapabilities().then((capabilities) => {
      if (!cancelled) setDemoGoal(capabilities.offline_demo ? capabilities.task_demo_goal : null);
    }).catch(() => { if (!cancelled) setNotice("无法确认离线演示能力，固定示例入口暂不可用。"); });
    return () => { cancelled = true; };
  }, [owner]);

  const remember = useCallback((id: string) => {
    if (!owner) return;
    try { localStorage.setItem(`task-workspace:v1:${owner}`, id); }
    catch { setNotice("任务已保存到服务端；浏览器无法保存入口，请保留当前任务链接。"); }
  }, [owner]);

  useEffect(() => {
    if (!owner) return;
    if (!selectedId) {
      try {
        const saved = localStorage.getItem(`task-workspace:v1:${owner}`);
        if (saved && UUID.test(saved)) router.replace(`/tasks?task=${saved}`);
      } catch { setNotice("浏览器无法读取最近任务，请输入任务标识打开。"); }
      return;
    }
    if (!UUID.test(selectedId)) { setError("任务标识格式不正确。"); setTask(null); return; }
    let cancelled = false;
    setLoading(true);
    setError("");
    setTask(null);
    api.getTask(selectedId).then((data) => {
      if (cancelled) return;
      setTask(data); setEditing(false); setConfirmed(false); remember(data.id);
    }).catch((err) => { if (!cancelled) setError(errorMessage(err, "无法读取任务，请核实标识后重试。")); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selectedId, owner, router, remember]);

  async function act(name: string, action: () => Promise<TaskRecord>) {
    if (mutation.current) return;
    mutation.current = true;
    setPending(name); setError(""); setNotice(""); setConfirmed(false);
    try {
      const next = await action();
      setTask(next); setEditing(false); remember(next.id);
      if (selectedId !== next.id) router.replace(`/tasks?task=${next.id}`);
    } catch (err) {
      const message = errorMessage(err, "请求结果尚未确认，请刷新任务状态后再决定下一步。");
      setError(message);
      if (task) {
        try {
          const latest = await api.getTask(task.id);
          setTask(latest);
          if (err instanceof ApiError && err.status === 409) setNotice("已读取最新版本，请核实后重新选择操作；本次未自动重试。");
        } catch { setNotice("最新状态暂时无法读取，请使用刷新状态；不要重复提交。 "); }
      }
    } finally { mutation.current = false; setPending(null); }
  }

  function openTask(event: FormEvent) {
    event.preventDefault();
    if (!UUID.test(lookup.trim())) { setError("请输入有效的任务标识（UUID）。"); return; }
    setError(""); router.replace(`/tasks?task=${lookup.trim()}`);
  }

  function newTask() {
    if (owner) {
      try { localStorage.removeItem(`task-workspace:v1:${owner}`); }
      catch { setNotice("无法清除浏览器最近任务入口；现有任务仍保留在服务端。"); }
    }
    setTask(null); setEditing(false); setError(""); setConfirmed(false); router.replace("/tasks");
  }

  const evidenceExpired = Object.values(task?.results ?? {}).some((receipt) => receipt.valid_until <= clock);
  const active = task?.status === "planning" || task?.status === "running";
  const exhausted = (task?.planning_attempts ?? 0) >= 2;
  const canPlan = task && ["pending", "needs_replan"].includes(task.status) && !exhausted;

  return <div className="mx-auto flex max-w-5xl flex-col gap-6 p-4 sm:p-6">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div><h1 className="text-2xl font-semibold">任务工作台</h1><p className="mt-1 text-sm text-muted-foreground">把复杂查询拆成可核实的步骤，每次由您决定继续执行。</p></div>
      {task && <Button variant="outline" disabled={!!pending} onClick={newTask}>新建任务</Button>}
    </header>
    <form onSubmit={openTask} className="flex flex-wrap items-end gap-2">
      <div className="flex min-w-0 flex-1 flex-col gap-1"><Label htmlFor="task-lookup">打开已有任务</Label><Input id="task-lookup" value={lookup} onChange={(e) => setLookup(e.target.value)} placeholder="输入任务标识（UUID）" /></div>
      <Button type="submit" variant="outline" disabled={!!pending || loading}>打开任务</Button>
    </form>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
    {loading ? <Skeleton className="h-48 w-full" /> : (!task || editing) ? (
      <TaskForm demoGoal={demoGoal ?? undefined} key={task?.id ?? "new"} initial={task ?? undefined} busy={!!pending} onCancel={task ? () => setEditing(false) : undefined} onSubmit={(input) => act(task ? "保存修改" : "创建任务", () => task ? api.updateTask(task.id, task.revision, input) : api.createTask(input))} />
    ) : (
      <>
        <Card>
          <CardHeader><div className="flex flex-wrap items-center gap-2"><CardTitle>{task.goal}</CardTitle><Badge variant="outline">{task.status === "completed" && evidenceExpired ? "曾完成 · 证据已过期" : TASK_STATUS_LABELS[task.status] ?? "状态待核实"}</Badge></div><CardDescription className="break-all">任务标识：{task.id} · 版本 {task.revision} · 已规划 {task.planning_attempts}/2 次</CardDescription></CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-wrap gap-2">
              {task.requirements.max_price != null && <Badge variant="secondary">单件预算 ≤ {formatPrice(task.requirements.max_price)}</Badge>}
              <Badge variant="secondary">至少 {task.requirements.min_items ?? 1} 件商品</Badge>
              {task.requirements.in_stock != null && <Badge variant="secondary">{task.requirements.in_stock ? "必须有货" : "只看缺货商品"}</Badge>}
              {task.requirements.compare && <Badge variant="secondary">需要商品对比</Badge>}
            </div>
            {!!task.constraints.length && <div><p className="text-sm font-medium">{task.status === "completed" ? "已由用户核实的文字约束" : "需人工核实的文字约束"}</p><ul className="mt-1 list-disc pl-5 text-sm text-muted-foreground">{task.constraints.map((c, i) => <li key={i}>{c}</li>)}</ul></div>}
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" disabled={!!pending} onClick={() => act("刷新状态", () => api.getTask(task.id))}><RefreshCw data-icon="inline-start" />刷新状态</Button>
              {canPlan && <Button disabled={!!pending} onClick={() => act("规划", () => api.planTask(task.id, task.revision))}>{task.status === "needs_replan" ? "重新规划" : "生成计划"}</Button>}
              {task.status === "ready" && !evidenceExpired && <Button disabled={!!pending} onClick={() => act("执行", () => api.nextTaskStep(task.id, task.revision))}>执行下一步</Button>}
              {evidenceExpired && ["ready", "completed", "needs_review"].includes(task.status) && <Button disabled={!!pending} onClick={() => act("重新核实", () => api.nextTaskStep(task.id, task.revision))}>重新核实过期证据</Button>}
              {active && <Button variant="outline" disabled={!!pending} onClick={() => act("恢复", () => api.recoverTask(task.id, task.revision))}>核实并恢复中断任务</Button>}
              <Button variant="outline" disabled={!!pending || active} onClick={() => setEditing(true)}>修改目标与约束</Button>
            </div>
            {evidenceExpired && <p role="status" className="text-sm text-muted-foreground">证据已过期，以下结果仅记录此前核验时的事实。请重新核实后再据此决策。</p>}
            {pending && <p role="status" className="flex items-center gap-2 text-sm"><Loader2 className="size-4 animate-spin" />正在{pending}，请勿重复提交…</p>}
            {active && <p className="text-sm text-muted-foreground">服务端仍标记为执行中。恢复只对已经中断的请求生效，活跃请求会拒绝恢复。</p>}
            {exhausted && ["pending", "needs_replan"].includes(task.status) && <p role="status" className="text-sm text-muted-foreground">已达到两次规划上限，请查看历史证据并新建任务；修改约束不会重置额度。</p>}
            {task.status === "needs_review" && <fieldset className="flex flex-col gap-3 rounded-lg border p-3"><legend className="px-1 text-sm font-medium">结构化条件已通过，以下任务要求仍需您核实</legend><ul className="list-disc pl-5 text-sm">{(task.review_items ?? task.constraints).map((item, index) => <li key={index}>{item}</li>)}</ul><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />我已逐项核实以上任务要求</label><Button disabled={!confirmed || !!pending || evidenceExpired} onClick={() => act("确认", () => api.confirmTask(task.id, task.revision))}>确认要求并完成任务</Button></fieldset>}
            {task.status === "completed" && <p role="status" className="text-sm">{evidenceExpired ? "服务端此前确认过任务完成，当前事实需重新核实。" : "服务端已确认任务完成。"}下方回执保留验收证据与有效期；这不是售后写操作凭证。</p>}
          </CardContent>
        </Card>
        {task.plan ? <section aria-label="任务计划" className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold">计划与步骤证据</h2>
          {task.plan.source === "deterministic_demo" && <p className="text-sm text-muted-foreground">固定演示计划 · 真实数据库查询。此示例不代表实时模型的自由规划能力。</p>}
          {task.plan.steps.map((step, index) => {
            const receipt = task.results[step.id];
            const verified = receipt?.verification?.passed === true;
            const expired = receipt && receipt.valid_until <= clock;
            return <Card key={step.id}>
              <CardHeader><div className="flex flex-wrap items-center gap-2"><CardTitle>第 {index + 1} 步 · {SERVICE_LABELS[step.service] ?? step.service}</CardTitle><Badge variant={receipt ? "secondary" : "outline"}>{receipt ? (expired ? "证据已过期" : verified ? "已核验" : "回执待核实") : task.active_step === step.id ? "执行中" : "待执行"}</Badge></div><CardDescription>{step.instruction}</CardDescription></CardHeader>
              <CardContent className="flex flex-col gap-3 text-sm">
                <p>依赖：{step.depends_on.length ? step.depends_on.join("、") : "无前置步骤"} · 步骤标识：{step.id}</p>
                {step.acceptance && <p className="text-muted-foreground">验收：{ACCEPTANCE_LABELS[step.acceptance.kind] ?? step.acceptance.kind}；至少 {step.acceptance.min_items} 项；{step.acceptance.required_ids.length ? `指定对象 ${step.acceptance.required_ids.join("、")}` : "按任务条件检查"}</p>}
                {receipt && <ReceiptView receipt={receipt} />}
              </CardContent>
            </Card>;
          })}
        </section> : <EmptyState icon={ListChecks} title="尚未生成计划" description="生成计划后先审阅步骤及依赖，再逐步执行。" />}
        {!!task.receipt_history.length && <details className="rounded-lg border p-4"><summary className="cursor-pointer text-sm font-medium">历史与失效证据（{task.receipt_history.length}）</summary><p className="mt-2 text-sm text-muted-foreground">历史失败或已失效的回执不算当前任务完成。</p><pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(task.receipt_history, null, 2)}</pre></details>}
      </>
    )}
  </div>;
}

function ReceiptView({ receipt }: { receipt: TaskReceipt }) {
  return <div className="flex flex-col gap-2">
    {receipt.result.response && <p className="whitespace-pre-wrap break-words">{receipt.result.response}</p>}
    <p className="break-all text-xs text-muted-foreground">回执：{receipt.receipt_id} · 有效期至 {new Date(receipt.valid_until * 1000).toLocaleString("zh-CN")}</p>
    {receipt.verification && <><p>核验范围：{receipt.verification.validation_scope.map(taskScopeLabel).join("、") || "请查看证据"}</p>{receipt.verification.errors.length > 0 && <p className="text-destructive">{receipt.verification.errors.map(taskErrorLabel).join("；")}</p>}</>}
    <details><summary className="cursor-pointer text-primary">查看事实证据</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-muted p-3 text-xs">{JSON.stringify(receipt.verification ?? receipt.result, null, 2)}</pre></details>
  </div>;
}

function TaskForm({ initial, busy, onSubmit, onCancel, demoGoal }: { demoGoal?: string; initial?: TaskInput; busy: boolean; onSubmit: (input: TaskInput) => void; onCancel?: () => void }) {
  const [goal, setGoal] = useState(initial?.goal ?? "");
  const [constraints, setConstraints] = useState(initial?.constraints.join("\n") ?? "");
  const [maxPrice, setMaxPrice] = useState(initial?.requirements.max_price?.toString() ?? "");
  const [minItems, setMinItems] = useState(String(initial?.requirements.min_items ?? 1));
  const [productIds, setProductIds] = useState(initial?.requirements.product_ids?.join("\n") ?? "");
  const [stockChoice, setStockChoice] = useState(initial?.requirements.in_stock == null ? "any" : initial.requirements.in_stock ? "available" : "unavailable");
  const [compare, setCompare] = useState(initial?.requirements.compare ?? false);
  const [error, setError] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    const ids = productIds.split(/[\s,，]+/).filter(Boolean);
    const lines = constraints.split("\n").map((v) => v.trim()).filter(Boolean);
    if (!goal.trim() || !Number.isInteger(Number(minItems)) || Number(minItems) < 1 || Number(minItems) > 20 || ids.length > 20 || (maxPrice !== "" && (!Number.isFinite(Number(maxPrice)) || Number(maxPrice) < 0)) || lines.length > 30 || ids.some((id) => !UUID.test(id))) {
      setError("请填写目标，使用有效预算和正整数数量；商品标识须为 UUID，文字约束不超过 30 行。"); return;
    }
    setError("");
    const requirements = { ...initial?.requirements, min_items: Number(minItems), product_ids: ids, in_stock: stockChoice === "any" ? null : stockChoice === "available", compare };
    if (maxPrice === "") delete requirements.max_price;
    else requirements.max_price = Number(maxPrice);
    onSubmit({ goal: goal.trim(), constraints: lines, requirements });
  }
  return <Card><CardHeader><CardTitle>{initial ? "修改目标与约束" : "创建查询任务"}</CardTitle><CardDescription>{initial ? "修改后旧计划和回执失效并保留历史，需要重新规划；累计规划次数不会重置。" : "结构化条件由服务端核验；其他文字约束需要您根据证据确认。"}</CardDescription></CardHeader><CardContent>
    <form onSubmit={submit} className="flex flex-col gap-4">
      {!initial && demoGoal && <Button type="button" variant="outline" disabled={busy} onClick={() => { setGoal(demoGoal); setMinItems("2"); setMaxPrice(""); setConstraints(""); setProductIds(""); setStockChoice("any"); setCompare(false); }}>离线固定示例（不调用模型）</Button>}
      <fieldset disabled={busy} className="flex min-w-0 flex-col gap-4"><legend className="sr-only">任务目标与约束</legend>
        <div className="flex flex-col gap-2"><Label htmlFor="task-goal">任务目标</Label><Textarea id="task-goal" value={goal} onChange={(e) => setGoal(e.target.value)} maxLength={6000} required placeholder="例如：找两款预算内的耳机，比较后核实库存" /></div>
        <div className="grid gap-4 sm:grid-cols-2"><div className="flex flex-col gap-2"><Label htmlFor="task-budget">单件预算上限（元，可选）</Label><Input id="task-budget" type="number" min="0" step="0.01" value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} /></div><div className="flex flex-col gap-2"><Label htmlFor="task-count">最少商品数</Label><Input id="task-count" type="number" min="1" max="20" step="1" value={minItems} onChange={(e) => setMinItems(e.target.value)} required /></div></div>
        <div className="flex flex-wrap gap-4"><div className="flex flex-col gap-2"><Label htmlFor="task-stock">库存条件</Label><Select items={STOCK_CHOICES} value={stockChoice} onValueChange={(value) => value && setStockChoice(value)}><SelectTrigger id="task-stock"><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{STOCK_CHOICES.map((choice) => <SelectItem key={choice.value} value={choice.value}>{choice.label}</SelectItem>)}</SelectGroup></SelectContent></Select></div><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} />需要商品对比</label></div>
        <div className="flex flex-col gap-2"><Label htmlFor="task-products">指定商品标识（可选，每行一个 UUID）</Label><Textarea id="task-products" value={productIds} onChange={(e) => setProductIds(e.target.value)} /></div>
        <div className="flex flex-col gap-2"><Label htmlFor="task-constraints">其他文字约束（可选，每行一条，需人工核实）</Label><Textarea id="task-constraints" value={constraints} onChange={(e) => setConstraints(e.target.value)} placeholder="例如：适合长时间佩戴" /></div>
      </fieldset>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex gap-2"><Button type="submit" disabled={busy || !goal.trim()}>{busy && <Loader2 data-icon="inline-start" className="animate-spin" />}{initial ? "保存修改并使旧计划失效" : "创建任务"}</Button>{onCancel && <Button type="button" variant="outline" disabled={busy} onClick={onCancel}>取消修改</Button>}</div>
    </form>
  </CardContent></Card>;
}
