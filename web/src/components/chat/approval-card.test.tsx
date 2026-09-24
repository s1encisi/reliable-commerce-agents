import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalCard } from "./approval-card";
import { api } from "@/lib/api";

afterEach(() => {
  vi.restoreAllMocks();
});

const RESUMED = {
  run_id: "run-1",
  approved: true,
  text: "Return approved. A replacement ships tomorrow.",
  agents_involved: ["order-management"],
};

describe("ApprovalCard", () => {
  it("运行暂停期间同时提供两种决定", () => {
    render(<ApprovalCard runId="run-1" onResolved={() => {}} />);

    expect(screen.getByRole("button", { name: /批准/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /拒绝/ })).toBeEnabled();
  });

  it("恢复运行并把恢复后的这一轮交还给对话流", async () => {
    const resume = vi.spyOn(api, "resumeRun").mockResolvedValue(RESUMED);
    const onResolved = vi.fn();
    const user = userEvent.setup();
    render(<ApprovalCard runId="run-1" onResolved={onResolved} />);

    await user.click(screen.getByRole("button", { name: /批准/ }));

    expect(resume).toHaveBeenCalledWith("run-1", true);
    await waitFor(() =>
      expect(onResolved).toHaveBeenCalledWith({
        approved: true,
        text: RESUMED.text,
        agentsInvolved: ["order-management"],
      })
    );
  });

  it("拒绝会以 false 透传，而不是被当成跳过调用", async () => {
    // 拒绝同样会恢复工作流 —— 它走的是另一条分支。完全不调用 resume
    // 会让这次运行永远停在暂停状态。
    const resume = vi.spyOn(api, "resumeRun").mockResolvedValue({ ...RESUMED, approved: false });
    const user = userEvent.setup();
    render(<ApprovalCard runId="run-9" onResolved={() => {}} />);

    await user.click(screen.getByRole("button", { name: /拒绝/ }));

    expect(resume).toHaveBeenCalledWith("run-9", false);
  });

  it("决定作出后用结果替换掉两个按钮", async () => {
    // 一个决定作出后还留着可点按钮，会引诱用户对已经翻篇的运行再次审批。
    vi.spyOn(api, "resumeRun").mockResolvedValue(RESUMED);
    const user = userEvent.setup();
    render(<ApprovalCard runId="run-1" onResolved={() => {}} />);

    await user.click(screen.getByRole("button", { name: /批准/ }));

    await waitFor(() => expect(screen.getByText(/已批准/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /拒绝/ })).not.toBeInTheDocument();
  });

  it("决定提交中时禁用两个按钮", async () => {
    let release: (v: typeof RESUMED) => void = () => {};
    vi.spyOn(api, "resumeRun").mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      })
    );
    const user = userEvent.setup();
    render(<ApprovalCard runId="run-1" onResolved={() => {}} />);

    await user.click(screen.getByRole("button", { name: /批准/ }));

    expect(screen.getByRole("button", { name: /拒绝/ })).toBeDisabled();
    release(RESUMED);
  });

  it("恢复失败时展示错误并保持可操作", async () => {
    // 悄悄退回两个可点按钮会掩盖「这个决定从未落地」的事实，
    // 这次运行会一直停在暂停状态而毫无提示。
    vi.spyOn(api, "resumeRun").mockRejectedValue(new Error("checkpoint not found"));
    const user = userEvent.setup();
    render(<ApprovalCard runId="run-1" onResolved={() => {}} />);

    await user.click(screen.getByRole("button", { name: /批准/ }));

    await waitFor(() => expect(screen.getByText(/checkpoint not found/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /批准/ })).toBeEnabled();
  });

  it("不会上报一个被后端拒绝的决议", async () => {
    vi.spyOn(api, "resumeRun").mockRejectedValue(new Error("nope"));
    const onResolved = vi.fn();
    const user = userEvent.setup();
    render(<ApprovalCard runId="run-1" onResolved={onResolved} />);

    await user.click(screen.getByRole("button", { name: /批准/ }));

    await waitFor(() => expect(screen.getByText(/nope/)).toBeInTheDocument());
    expect(onResolved).not.toHaveBeenCalled();
  });
});
