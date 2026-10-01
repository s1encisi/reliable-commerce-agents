import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TaskWorkspace } from "./page";
import { api, ApiError } from "@/lib/api";
import type { TaskRecord } from "@/lib/tasks";

const nav = vi.hoisted(() => ({ search: "", replace: vi.fn() }));
const auth = vi.hoisted(() => ({ user: { email: "customer@test" } }));
vi.mock("next/navigation", () => ({ useRouter: () => nav, useSearchParams: () => new URLSearchParams(nav.search) }));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => auth }));
const ID = "11111111-1111-4111-8111-111111111111";
const PRODUCT = "22222222-2222-4222-8222-222222222222";
function task(overrides: Partial<TaskRecord> = {}): TaskRecord {
  return { id: ID, revision: 3, status: "ready", goal: "查找耳机", constraints: [], requirements: { max_price: 500, min_items: 1 }, planning_attempts: 1, active_step: null, receipt_history: [], results: {}, plan: { goal: "查找耳机", constraints: [], steps: [{ id: "search", service: "product-discovery", instruction: "检索预算内商品", depends_on: [], acceptance: { kind: "product_selection", required_ids: [], min_items: 1, from_steps: [] } }] }, ...overrides };
}
beforeEach(() => { vi.spyOn(api, "getRuntimeCapabilities").mockResolvedValue({ offline_demo: true, task_demo_goal: "筛选两款商品并核对库存" }); nav.search = `task=${ID}`; nav.replace.mockClear(); localStorage.clear(); });
afterEach(() => vi.restoreAllMocks());

describe("任务工作台", () => {
  it("创建结构化条件并保留文字约束，不自动规划或执行", async () => {
    nav.search = "";
    const create = vi.spyOn(api, "createTask").mockResolvedValue(task({ status: "pending", plan: null, revision: 0, planning_attempts: 0 }));
    const plan = vi.spyOn(api, "planTask");
    render(<TaskWorkspace />);
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "找两款耳机" } });
    fireEvent.change(screen.getByLabelText(/单件预算上限/), { target: { value: "800" } });
    fireEvent.change(screen.getByLabelText("最少商品数"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText(/指定商品标识/), { target: { value: PRODUCT } });
    fireEvent.change(screen.getByLabelText(/其他文字约束/), { target: { value: "佩戴舒适" } });
    fireEvent.click(screen.getByLabelText("需要商品对比"));
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith({ goal: "找两款耳机", constraints: ["佩戴舒适"], requirements: { max_price: 800, min_items: 2, product_ids: [PRODUCT], in_stock: null, compare: true } }));
    expect(plan).not.toHaveBeenCalled();
    expect(nav.replace).toHaveBeenCalledWith(`/tasks?task=${ID}`);
  });
  it("读取计划后只在点击时推进当前版本的一步，并展示事实回执", async () => {
    vi.spyOn(api, "getTask").mockResolvedValue(task());
    const next = vi.spyOn(api, "nextTaskStep").mockResolvedValue(task({ revision: 5, status: "completed", results: { search: { receipt_id: "receipt-1", verified_at: 100, valid_until: 9999999999, result: { response: "找到了满足预算的商品" }, verification: { passed: true, errors: [], evidence: [{ id: PRODUCT, price: 299 }], validation_scope: ["预算"] } } } }));
    render(<TaskWorkspace />);
    await screen.findByRole("button", { name: "执行下一步" });
    expect(next).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "执行下一步" }));
    await waitFor(() => expect(next).toHaveBeenCalledWith(ID, 3));
    expect(await screen.findByText("找到了满足预算的商品")).toBeInTheDocument();
    expect(screen.getByText("已核验")).toBeInTheDocument();
    expect(screen.getByText(/"price": 299/)).toBeInTheDocument();
  });
  it("409 读取最新版本而不自动重放执行", async () => {
    const get = vi.spyOn(api, "getTask").mockResolvedValueOnce(task()).mockResolvedValueOnce(task({ revision: 9, status: "running", active_step: "search" }));
    const next = vi.spyOn(api, "nextTaskStep").mockRejectedValue(new ApiError(409, { detail: "版本冲突" }));
    render(<TaskWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "执行下一步" }));
    expect(await screen.findByText(/已读取最新版本/)).toBeInTheDocument();
    expect(screen.getByText(/版本 9/)).toBeInTheDocument();
    expect(next).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledTimes(2);
  });
  it("中断任务可显式恢复，但不自动执行下一步", async () => {
    vi.spyOn(api, "getTask").mockResolvedValue(task({ status: "running", active_step: "search" }));
    const recover = vi.spyOn(api, "recoverTask").mockResolvedValue(task({ revision: 4 }));
    const next = vi.spyOn(api, "nextTaskStep");
    render(<TaskWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "核实并恢复中断任务" }));
    await waitFor(() => expect(recover).toHaveBeenCalledWith(ID, 3));
    expect(await screen.findByRole("button", { name: "执行下一步" })).toBeEnabled();
    expect(next).not.toHaveBeenCalled();
  });
  it("文字约束必须由用户明确确认后才调用完成接口", async () => {
    vi.spyOn(api, "getTask").mockResolvedValue(task({ status: "needs_review", constraints: ["佩戴舒适"] }));
    const confirm = vi.spyOn(api, "confirmTask").mockResolvedValue(task({ status: "completed", revision: 4 }));
    render(<TaskWorkspace />);
    expect(await screen.findByRole("button", { name: "确认要求并完成任务" })).toBeDisabled();
    fireEvent.click(screen.getByLabelText("我已逐项核实以上任务要求"));
    fireEvent.click(screen.getByRole("button", { name: "确认要求并完成任务" }));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(ID, 3));
  });
  it("修改目标约束使用当前 revision，且不擅自清除未编辑字段", async () => {
    vi.spyOn(api, "getTask").mockResolvedValue(task({ requirements: { max_price: 500, min_items: 1, min_rating: 4, category: "耳机" } }));
    const update = vi.spyOn(api, "updateTask").mockResolvedValue(task({ status: "pending", plan: null, revision: 4 }));
    render(<TaskWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "修改目标与约束" }));
    fireEvent.change(screen.getByLabelText("任务目标"), { target: { value: "改为运动耳机" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改并使旧计划失效" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith(ID, 3, expect.objectContaining({ goal: "改为运动耳机", requirements: expect.objectContaining({ category: "耳机", min_rating: 4 }) })));
  });
  it("填入离线示例时不限库存，不把未选条件发送为缺货要求", async () => {
    nav.search = "";
    const create = vi.spyOn(api, "createTask").mockResolvedValue(task({ status: "pending", plan: null }));
    render(<TaskWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "离线固定示例（不调用模型）" }));
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ goal: "筛选两款商品并核对库存", requirements: expect.objectContaining({ min_items: 2, in_stock: null }) })));
  });

  it("模型计划没有文字约束时仍展示原始目标待人工核实", async () => {
    vi.spyOn(api, "getTask").mockResolvedValue(task({ status: "needs_review", constraints: [], review_items: ["任务目标：查找耳机"] }));
    render(<TaskWorkspace />);
    expect(await screen.findByText("任务目标：查找耳机")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认要求并完成任务" })).toBeDisabled();
  });

  it("未确认离线运行时不显示不调用模型的示例承诺", async () => {
    nav.search = "";
    vi.spyOn(api, "getRuntimeCapabilities").mockResolvedValue({ offline_demo: false, task_demo_goal: null });
    render(<TaskWorkspace />);
    await waitFor(() => expect(api.getRuntimeCapabilities).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "离线固定示例（不调用模型）" })).not.toBeInTheDocument();
  });

  it("已完成任务的回执过期后允许按当前版本显式重新核实", async () => {
    vi.spyOn(api, "getTask").mockResolvedValue(task({ status: "completed", revision: 8, results: { search: { receipt_id: "expired", verified_at: 1, valid_until: 2, result: { response: "历史结果" }, verification: { passed: true, errors: [], evidence: [], validation_scope: [] } } } }));
    const next = vi.spyOn(api, "nextTaskStep").mockResolvedValue(task({ revision: 10 }));
    render(<TaskWorkspace />);
    expect(await screen.findByText("曾完成 · 证据已过期")).toBeInTheDocument();
    expect(next).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重新核实过期证据" }));
    await waitFor(() => expect(next).toHaveBeenCalledWith(ID, 8));
    expect(await screen.findByRole("button", { name: "执行下一步" })).toBeEnabled();
  });

  it("规划额度耗尽时不再提供规划按钮", async () => {
    vi.spyOn(api, "getTask").mockResolvedValue(task({ status: "needs_replan", planning_attempts: 2 }));
    render(<TaskWorkspace />);
    expect(await screen.findByText(/已达到两次规划上限/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新规划" })).not.toBeInTheDocument();
  });
});
