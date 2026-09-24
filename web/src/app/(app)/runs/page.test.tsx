import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import RunsPage from "./page";
import { api, type RunEntry } from "@/lib/api";

const PENDING_RUN: RunEntry = {
  id: "run-1",
  agent_name: "orchestrator",
  user_email: "alice@example.com",
  user_name: "Alice",
  input_summary: "return order abc",
  tokens_in: 0,
  tokens_out: 0,
  tool_calls_count: 0,
  duration_ms: 300,
  status: "success",
  trace_id: null,
  created_at: new Date().toISOString(),
  steps: [], // 工作流模式不会产生 agent_execution_steps 行
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RunsPage —— 检查点续跑", () => {
  it("在折叠列表中即显示「待审批」徽章，无需先展开", async () => {
    // 回归测试：checkpoint/HITL 数据此前是展开时才懒加载的，
    // 导致这个本应能整列扫视的徽章，在行被打开过一次之前永远
    // 显示不出来。修复方式是对每个可见行提前拉取。
    vi.spyOn(api, "getRuns").mockResolvedValue({ entries: [PENDING_RUN], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "getRunCheckpoints").mockResolvedValue({
      run_id: "run-1",
      checkpoints: [],
      hitl_request: {
        id: "hitl-1",
        status: "pending",
        payload: { order_id: "order-abc" },
        response: null,
        created_at: new Date().toISOString(),
        responded_at: null,
      },
    });

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("待审批")).toBeInTheDocument());
    // 仍处于折叠状态 —— 尚未显示「批准」按钮。
    expect(screen.queryByRole("button", { name: "批准" })).not.toBeInTheDocument();
  });

  it("即使没有任何步骤，也显示「待审批」徽章并允许展开该行", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ entries: [PENDING_RUN], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "getRunCheckpoints").mockResolvedValue({
      run_id: "run-1",
      checkpoints: [{ checkpoint_id: "cp-1", workflow_name: "return-and-replace", created_at: new Date().toISOString() }],
      hitl_request: {
        id: "hitl-1",
        status: "pending",
        payload: { order_id: "order-abc", order_total: 720 },
        response: null,
        created_at: new Date().toISOString(),
        responded_at: null,
      },
    });

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());

    fireEvent.click(screen.getByText("return order abc"));
    await waitFor(() => expect(screen.getByText(/退货审批 —— 待审批/)).toBeInTheDocument());
    expect(screen.getByText(/订单 order-abc/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批准" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeInTheDocument();
  });

  it("approving calls resumeRun and refreshes to show the resolved state", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ entries: [PENDING_RUN], total: 1, limit: 20, offset: 0 });
    const pending = {
      run_id: "run-1",
      checkpoints: [],
      hitl_request: {
        id: "hitl-1",
        status: "pending" as const,
        payload: { order_id: "order-abc" },
        response: null,
        created_at: new Date().toISOString(),
        responded_at: null,
      },
    };
    const resolved = {
      ...pending,
      hitl_request: { ...pending.hitl_request, status: "approved" as const, responded_at: new Date().toISOString() },
    };
    const getCheckpoints = vi.spyOn(api, "getRunCheckpoints").mockResolvedValueOnce(pending).mockResolvedValueOnce(resolved);
    const resume = vi.spyOn(api, "resumeRun").mockResolvedValue({
      run_id: "run-1",
      approved: true,
      text: "Return for order order-abc approved and finalized.",
      agents_involved: ["check-eligibility", "finalize"],
    });

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());
    fireEvent.click(screen.getByText("return order abc"));
    await waitFor(() => expect(screen.getByRole("button", { name: "批准" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "批准" }));

    await waitFor(() => expect(resume).toHaveBeenCalledWith("run-1", true));
    await waitFor(() => expect(screen.getByText(/退货审批 —— 已批准/)).toBeInTheDocument());
    expect(getCheckpoints).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "批准" })).not.toBeInTheDocument();
  });

  it("shows a resume error inline without crashing", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ entries: [PENDING_RUN], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "getRunCheckpoints").mockResolvedValue({
      run_id: "run-1",
      checkpoints: [],
      hitl_request: {
        id: "hitl-1",
        status: "pending",
        payload: {},
        response: null,
        created_at: new Date().toISOString(),
        responded_at: null,
      },
    });
    vi.spyOn(api, "resumeRun").mockRejectedValue(new Error("No pending approval found for this run"));

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());
    fireEvent.click(screen.getByText("return order abc"));
    await waitFor(() => expect(screen.getByRole("button", { name: "拒绝" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    await waitFor(() => expect(screen.getByText("No pending approval found for this run")).toBeInTheDocument());
  });
});
