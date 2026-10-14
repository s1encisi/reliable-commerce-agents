import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProcurementWorkspace } from "./page";
import { ApiError } from "@/lib/api";
import * as procurement from "@/lib/procurement";
import { procurementApi, type ProcurementTask, type ProcurementEvent } from "@/lib/procurement";

const nav = vi.hoisted(() => ({ search: "", replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => nav, useSearchParams: () => new URLSearchParams(nav.search) }));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ user: { email: "customer@example.test" } }) }));
const ID = "11111111-1111-4111-8111-111111111111";
const spec = { query: "采购专用耳机", quantity: 2, alternative_count: 2, total_budget: "300.00", currency: "CNY" as const, in_stock: true, min_rating: 4, preferences: [], budget_scope: "goods" as const, quote_policy: "single_coupon_v1" as const };
function task(overrides: Partial<ProcurementTask> = {}): ProcurementTask {
  return { id: ID, task_type: "procurement", goal: "买2副采购专用耳机，总预算300元，2个备选", execution_mode: "offline", status: "draft", revision: 0, state_seq: 1, spec_revision: 0, plan_revision: 0, requirements: null, draft_spec: { ...spec, _parse: { source: "rule_based_offline", questions: [], unsupported_conditions: [] } }, plan: null, steps: [], artifacts: [], options: [], counters: { model_calls: 0, planning_attempts: 0, repairs_used: 0, step_attempts: 0, reused_steps: 0 }, stop_reason: null, ...overrides };
}
let emit: (event: ProcurementEvent) => void;
beforeEach(() => {
  nav.search = `task=${ID}`; nav.replace.mockClear(); localStorage.clear();
  vi.spyOn(procurementApi, "capabilities").mockResolvedValue({ procurement: { enabled: true, mode: "offline", model_authorized: false, currency: "CNY", quote_policy: "single_coupon_v1", write_operations: false } });
  vi.spyOn(procurement, "streamProcurement").mockImplementation((_id, _seq, signal, callback) => { emit = callback; return new Promise((resolve) => signal.addEventListener("abort", () => resolve())); });
});
afterEach(() => vi.restoreAllMocks());

describe("采购工作台合同测试（不替代真实后端验收）", () => {
  it("解析只产生草案，数量预算缺失时禁止确认", async () => {
    nav.search = "";
    const parse = vi.spyOn(procurementApi, "parse").mockResolvedValue(task({ draft_spec: { query: "耳机", quantity: null, total_budget: null } }));
    const start = vi.spyOn(procurementApi, "start");
    render(<ProcurementWorkspace />);
    fireEvent.change(screen.getByLabelText("采购需求"), { target: { value: "买办公耳机" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "解析为需求草案" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "解析为需求草案" }));
    await waitFor(() => expect(parse).toHaveBeenCalledWith("买办公耳机"));
    expect(await screen.findByRole("button", { name: "确认并自动启动" })).toBeDisabled();
    expect(start).not.toHaveBeenCalled();
    expect(screen.getByText(/规则解析 \/ 固定离线计划 \/ 真实数据库工具/)).toBeInTheDocument();
  });
  it("解析失败展示错误，不能因切换代次吞掉失败", async () => {
    nav.search = ""; vi.spyOn(procurementApi, "parse").mockRejectedValue(new ApiError(422, { detail: "无法解析数量" }));
    render(<ProcurementWorkspace />);
    fireEvent.change(screen.getByLabelText("采购需求"), { target: { value: "买耳机" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "解析为需求草案" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "解析为需求草案" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法解析数量");
  });
  it("人工核对后按返回revision串行保存、确认、启动；不发送_parse", async () => {
    vi.spyOn(procurementApi, "get").mockResolvedValue(task());
    const update = vi.spyOn(procurementApi, "update").mockResolvedValue(task({ revision: 1, state_seq: 2 }));
    const confirm = vi.spyOn(procurementApi, "confirm").mockResolvedValue(task({ revision: 2, state_seq: 3, status: "pending" }));
    const start = vi.spyOn(procurementApi, "start").mockResolvedValue(task({ revision: 3, state_seq: 4, status: "queued" }));
    render(<ProcurementWorkspace />);
    expect(await screen.findByRole("button", { name: "确认并自动启动" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(screen.getByRole("button", { name: "确认并自动启动" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith(ID, 2));
    expect(update).toHaveBeenCalledWith(ID, 0, expect.stringContaining("总货款预算 300.00 元"), spec);
    expect(confirm).toHaveBeenCalledWith(ID, 1, spec);
    expect(start).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(procurement.streamProcurement).toHaveBeenCalledTimes(2)); // Draft streams end; start must open a live reader.
    expect(await screen.findByText("当前任务 · 等待执行")).toBeInTheDocument();
  });
  it("无法支持的条件必须修改原需求重解析", async () => {
    vi.spyOn(procurementApi, "get").mockResolvedValue(task({ draft_spec: { ...spec, _parse: { source: "rule_based_offline", questions: [], unsupported_conditions: ["预算包含税费"] } } }));
    render(<ProcurementWorkspace />);
    expect(await screen.findByRole("alert")).toHaveTextContent("预算包含税费");
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "确认并自动启动" })).toBeDisabled();
  });
  it("暂停后预算修改同步goal，修改字段后必须重新核对", async () => {
    vi.spyOn(procurementApi, "get").mockResolvedValue(task({ status: "paused", revision: 7, spec_revision: 1, requirements: spec }));
    const update = vi.spyOn(procurementApi, "update").mockResolvedValue(task({ revision: 8 }));
    vi.spyOn(procurementApi, "confirm").mockResolvedValue(task({ revision: 9, status: "pending" }));
    vi.spyOn(procurementApi, "start").mockResolvedValue(task({ revision: 10, status: "queued" }));
    render(<ProcurementWorkspace />);
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.change(screen.getByLabelText("每个方案总货款预算（元）"), { target: { value: "240" } });
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(screen.getByRole("button", { name: "确认并自动启动" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith(ID, 7, expect.stringContaining("240.00 元"), { ...spec, total_budget: "240.00" }));
  });
  it("409读取新版本，不自动重放暂停请求", async () => {
    const get = vi.spyOn(procurementApi, "get").mockResolvedValueOnce(task({ status: "running", revision: 4 })).mockResolvedValue(task({ status: "paused", revision: 5 }));
    const pause = vi.spyOn(procurementApi, "pause").mockRejectedValue(new ApiError(409, {}));
    render(<ProcurementWorkspace />); fireEvent.click(await screen.findByRole("button", { name: "暂停任务" }));
    expect(await screen.findByText(/本次操作没有自动重试/)).toBeInTheDocument();
    expect(pause).toHaveBeenCalledTimes(1); expect(get).toHaveBeenCalledTimes(2);
  });
  it("晚到GET不能把新版暂停任务退回执行中，续连传递收到的seq", async () => {
    let resolveOld!: (value: ProcurementTask) => void;
    vi.spyOn(procurementApi, "get").mockResolvedValueOnce(task({ status: "running", revision: 4, state_seq: 9 })).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    vi.spyOn(procurementApi, "pause").mockResolvedValue(task({ status: "paused", revision: 5, state_seq: 11 }));
    render(<ProcurementWorkspace />); await screen.findByRole("button", { name: "暂停任务" });
    act(() => emit({ seq: 10, event: "step.finished", created_at: "", payload: { task_id: ID, revision: 4, spec_revision: 1, plan_revision: 1, status: "running" } }));
    fireEvent.click(screen.getByRole("button", { name: "暂停任务" }));
    await screen.findByText("当前任务 · 已暂停");
    await act(async () => resolveOld(task({ status: "running", revision: 4, state_seq: 10 })));
    expect(screen.getByText("当前任务 · 已暂停")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新连接事件" }));
    await waitFor(() => expect(procurement.streamProcurement).toHaveBeenLastCalledWith(ID, 10, expect.any(AbortSignal), expect.any(Function)));
  });
  it("事件流在最终事件之前结束时仍读取权威终态", async () => {
    const get = vi.spyOn(procurementApi, "get").mockResolvedValueOnce(task({ status: "running", revision: 4, state_seq: 9 })).mockResolvedValue(task({ status: "completed", revision: 4, state_seq: 12 }));
    vi.mocked(procurement.streamProcurement).mockResolvedValueOnce();
    render(<ProcurementWorkspace />);
    expect(await screen.findByText("当前任务 · 方案已核验")).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  });
  it("独立显示方案数量和报价、原文引用及不足证据", async () => {
    const option = { product_id: "p1", name: "合成耳机一", quantity: 2, quote: { unit_price: "100.00", original_total: "200.00", discount_amount: "20.00", final_total: "180.00", coupon_id: "c1", rule_hash: "hash" }, inventory: { available_quantity: 8, requested_quantity: 2, sufficient: true }, review: { evidence_status: "available", reviews: [{ id: "r1", title: "通话反馈", body: "办公室使用正常", rating: 4 }], risk_references: [] } };
    vi.spyOn(procurementApi, "get").mockResolvedValue(task({ status: "completed", options: [option, { ...option, product_id: "p2", name: "合成耳机二", review: { evidence_status: "insufficient", reviews: [], risk_references: [] } }] }));
    render(<ProcurementWorkspace />);
    expect(await screen.findByText("方案 1 · 合成耳机一")).toBeInTheDocument();
    expect(screen.getByText("方案 2 · 合成耳机二")).toBeInTheDocument();
    expect(screen.getByText("评论引用 r1")).toBeInTheDocument();
    expect(screen.getByText(/评论证据不足/)).toBeInTheDocument();
    expect(screen.getByText(/两方案不合并计算/)).toBeInTheDocument();
  });
  it("修复额度耗尽后展示失败依据，并通过显式修订入口进入暂停态", async () => {
    vi.spyOn(procurementApi, "get").mockResolvedValue(task({ status: "needs_review", revision: 8, stop_reason: "REPAIR_LIMIT", receipt_history: [{ kind: "repair_requested", plan_revision: 2, failures: [{ step_id: "stock-1", reason: "候选库存不足以购买确认数量", affected_product_ids: ["product-1"] }] }] }));
    const pause = vi.spyOn(procurementApi, "pause").mockResolvedValue(task({ status: "paused", revision: 9 }));
    render(<ProcurementWorkspace />);
    expect(await screen.findByText(/自动修复额度已用尽/)).toBeInTheDocument();
    expect(screen.getByText("候选库存不足以购买确认数量")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "修订需求" }));
    await waitFor(() => expect(pause).toHaveBeenCalledWith(ID, 8));
    expect(await screen.findByRole("button", { name: "确认并自动启动" })).toBeDisabled();
  });
  it("模型模式未授权时禁止解析", async () => {
    nav.search = ""; vi.mocked(procurementApi.capabilities).mockResolvedValue({ procurement: { enabled: true, mode: "model", model_authorized: false, currency: "CNY", quote_policy: "single_coupon_v1", write_operations: false } });
    render(<ProcurementWorkspace />);
    expect(await screen.findByText(/尚未授权模型调用预算/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("采购需求"), { target: { value: "买2副耳机300元" } });
    expect(screen.getByRole("button", { name: "解析为需求草案" })).toBeDisabled();
  });
});
