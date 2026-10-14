import { api, apiRequest, apiUrl, ApiError } from "./api";

export interface ProcurementSpec {
  query: string;
  quantity: number;
  alternative_count: number;
  total_budget: string;
  currency: "CNY";
  in_stock: boolean;
  min_rating: number | null;
  preferences: string[];
  budget_scope: "goods";
  quote_policy: "single_coupon_v1";
}
export interface ProcurementDraft extends Partial<Omit<ProcurementSpec, "quantity" | "total_budget">> {
  quantity?: number | null;
  total_budget?: string | null;
  _parse?: { source: "rule_based_offline" | "model" | "user_revised" | "user_structured"; questions: string[]; unsupported_conditions: string[] };
}
export interface ProcurementPlanStep {
  id: string; capability: string; instruction: string; parameters: Record<string, unknown>; depends_on: string[];
}
export interface ProcurementOutcome {
  status: string; data: Record<string, unknown>; error_code?: string | null; message: string;
  retryable: boolean; source: string; artifacts: unknown[];
}
export interface ProcurementArtifact {
  artifact_id: string; step_id: string; plan_revision: number; capability: string; source: string;
  observed_at: string; valid_until: string; data: Record<string, unknown>;
}
export interface ProcurementOption {
  product_id: string; name: string; quantity: number;
  quote: { unit_price: string; original_total: string; discount_amount: string; final_total: string; coupon_id: string | null; rule_hash: string };
  inventory: { available_quantity: number; requested_quantity: number; sufficient: boolean };
  review: {
    reviews: { id: string; title: string; body: string; rating: number }[];
    evidence_status: string;
    risk_references: { review_id: string; reason: string; quote: string }[];
    analysis_scope?: string;
  };
}
export interface ProcurementTask {
  id: string; task_type: "procurement"; goal: string; execution_mode: "offline" | "model";
  requirements: ProcurementSpec | null; draft_spec: ProcurementDraft | null;
  status: string; revision: number; state_seq: number; spec_revision: number; plan_revision: number;
  plan: { steps: ProcurementPlanStep[]; source: "deterministic_demo" | "model"; rationale: string } | null;
  steps: { id: string; capability: string; status: string; attempt?: number; plan_revision: number; input_hash: string; outcome: ProcurementOutcome | null; updated_at: string; reused_from?: unknown }[];
  artifacts: ProcurementArtifact[]; options: ProcurementOption[];
  counters: { model_calls: number; planning_attempts: number; repairs_used: number; step_attempts: number; reused_steps: number };
  stop_reason: string | null;
  receipt_history?: { kind: string; plan_revision?: number; failures?: { code?: string; reason?: string; step_id?: string; affected_product_ids?: string[] }[]; [key: string]: unknown }[];
}
export interface ProcurementCapabilities {
  enabled: boolean; mode: "offline" | "model"; model_authorized: boolean;
  currency: "CNY"; quote_policy: "single_coupon_v1"; write_operations: false;
}
export interface ProcurementEvent {
  seq: number; event: string; created_at: string;
  payload: { task_id: string; revision: number; spec_revision: number; plan_revision: number; status: string; [key: string]: unknown };
}

const command = (id: string, action: string, body: unknown) => apiRequest<ProcurementTask>(`/api/tasks/${encodeURIComponent(id)}/${action}`, { method: "POST", body: JSON.stringify(body) });
export const procurementApi = {
  capabilities: () => apiRequest<{ procurement?: ProcurementCapabilities }>("/api/runtime/capabilities"),
  parse: (goal: string) => apiRequest<ProcurementTask>("/api/tasks/parse", { method: "POST", body: JSON.stringify({ goal }) }),
  get: (id: string) => apiRequest<ProcurementTask>(`/api/tasks/${encodeURIComponent(id)}`),
  confirm: (id: string, revision: number, requirements: ProcurementSpec) => command(id, "confirm-spec", { revision, requirements }),
  start: (id: string, revision: number) => command(id, "start", { revision }),
  pause: (id: string, revision: number) => command(id, "pause", { revision }),
  update: (id: string, revision: number, goal: string, requirements: ProcurementSpec) => apiRequest<ProcurementTask>(`/api/tasks/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ revision, goal, constraints: [], requirements }) }),
};

/** Control revision and background sequence both matter; GET can finish after a newer command. */
export function reconcileProcurement(current: ProcurementTask | null, incoming: ProcurementTask): ProcurementTask {
  if (!current) return incoming;
  if (current.id !== incoming.id) return current;
  if (incoming.revision < current.revision || (incoming.revision === current.revision && incoming.state_seq < current.state_seq)) return current;
  return incoming;
}

export const PROCUREMENT_STATUS: Record<string, string> = {
  draft: "待核对需求", needs_input: "需要补充需求", pending: "需求已确认", queued: "等待执行", planning: "正在规划",
  running: "正在执行", pausing: "正在暂停", paused: "已暂停", needs_review: "需要人工核实", completed: "方案已核验",
  failed: "本次未完成", budget_exhausted: "执行预算已耗尽", needs_replan: "需要修复计划",
};
export const STOP_REASONS: Record<string, string> = {
  REPAIR_LIMIT: "本次需求的自动修复额度已用尽，请核对失败证据并修订需求。",
  INVALID_REPAIR_PLAN: "修复计划未通过契约校验，需要核对需求与失败证据。",
  INVALID_PLAN: "规划结果未通过契约校验，尚未生成有效采购方案。",
  PLANNING_LIMIT: "本次需求的规划额度已用尽，请核对后修订需求。",
  MODEL_NOT_AUTHORIZED: "当前没有有效的模型调用授权，执行已停止。",
  MODEL_CALL_LIMIT: "本任务模型调用额度已耗尽，未继续调用模型。",
  NOT_ALLOWLISTED: "当前账号未获本地采购实验权限。",
  RUN_DEADLINE_EXCEEDED: "执行已达到时间上限，请查看已保存的证据。",
  RUN_FAILED: "本次执行未完成，请查看节点结果与诊断。",
  FINALIZER_REQUIRED: "缺少最终核验结果，不能将方案视为已完成。",
};
export const CAPABILITY_LABELS: Record<string, string> = { search_candidates: "检索候选商品", quote_options: "核实货款与优惠", check_inventory: "核实可售数量", review_evidence: "收集评论证据" };
export const STEP_STATUS: Record<string, string> = { pending: "待执行", running: "执行中", succeeded: "已取得证据", failed: "核验未通过", blocked: "受阻", unknown: "结果未知", skipped: "已跳过" };
export const ACTIVE_STATUSES = new Set(["queued", "planning", "running", "pausing"]);
export const EDITABLE_STATUSES = new Set(["draft", "needs_input", "paused"]);

/** Keep monetary input decimal, and explicitly whitelist protocol fields (exclude _parse). */
export function buildProcurementSpec(values: { query: string; quantity: string; alternatives: string; budget: string; rating: string; preferences: string }): ProcurementSpec | null {
  const quantity = Number(values.quantity), alternatives = Number(values.alternatives);
  const budget = values.budget.trim();
  const rating = values.rating.trim() === "" ? null : Number(values.rating);
  if (!values.query.trim() || !Number.isInteger(quantity) || quantity < 1 || quantity > 100 || ![1, 2].includes(alternatives)) return null;
  if (!/^\d{1,12}(\.\d{1,2})?$/.test(budget) || !/[1-9]/.test(budget) || (rating !== null && (!Number.isFinite(rating) || rating < 0 || rating > 5))) return null;
  const [whole, fraction = ""] = budget.split(".");
  return { query: values.query.trim(), quantity, alternative_count: alternatives, total_budget: `${whole.replace(/^0+(?=\d)/, "")}.${fraction.padEnd(2, "0")}`, currency: "CNY", in_stock: true, min_rating: rating, preferences: values.preferences.split("\n").map((s) => s.trim()).filter(Boolean), budget_scope: "goods", quote_policy: "single_coupon_v1" };
}

export function procurementGoal(spec: ProcurementSpec): string {
  return `采购 ${spec.quantity} 件${spec.query}，总货款预算 ${spec.total_budget} 元，提供 ${spec.alternative_count} 个独立备选方案，必须现货。${spec.min_rating === null ? "" : `最低评分 ${spec.min_rating}。`}${spec.preferences.length ? `偏好：${spec.preferences.join("；")}。` : ""}币种人民币，仅单券优惠，货款不含运费和税费，不下单。`;
}

export function planLayers(steps: ProcurementPlanStep[]): ProcurementPlanStep[][] {
  const layers: ProcurementPlanStep[][] = [], done = new Set<string>();
  let remaining = [...steps];
  while (remaining.length) {
    const layer = remaining.filter((s) => s.depends_on.every((id) => done.has(id)));
    if (!layer.length) return [...layers, remaining]; // Unknown graph remains visible for diagnosis.
    layers.push(layer); layer.forEach((s) => done.add(s.id)); remaining = remaining.filter((s) => !done.has(s.id));
  }
  return layers;
}

/** Read-only resumable stream. Reconnection is an explicit UI action, never a mutation retry. */
export async function streamProcurement(id: string, afterSeq: number, signal: AbortSignal, onEvent: (event: ProcurementEvent) => void): Promise<void> {
  let refreshed = false;
  let response: Response;
  while (true) {
    const token = api.getToken();
    response = await fetch(apiUrl(`/api/tasks/${encodeURIComponent(id)}/events?after_seq=${afterSeq}`), { signal, headers: { Accept: "text/event-stream", "Last-Event-ID": String(afterSeq), ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
    if (response.status !== 401 || refreshed) break;
    await procurementApi.get(id); // Uses the shared read-only authentication refresh path.
    refreshed = true;
  }
  if (!response.ok) throw new ApiError(response.status, {});
  if (!response.body) throw new Error("服务没有返回事件流，请手动刷新任务。");
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = "", cursor = afterSeq;
  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let match: RegExpExecArray | null;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, match.index); buffer = buffer.slice(match.index + match[0].length);
        const data = frame.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        const event = JSON.parse(data) as ProcurementEvent;
        if (!Number.isSafeInteger(event.seq) || !event.payload || event.payload.task_id !== id || event.seq <= cursor) continue;
        cursor = event.seq; onEvent(event);
      }
    }
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}
