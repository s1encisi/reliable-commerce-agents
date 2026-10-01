import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConversationContextEditor } from "./conversation-context";
import { api, ApiError } from "@/lib/api";

const initial = { conversation_id: "c-1", revision: 2, context: { goal: "找耳机", constraints: ["预算五百", "有库存"] } };
afterEach(() => vi.restoreAllMocks());
async function openEditor() {
  const details = screen.getByText("目标与约束").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  await waitFor(() => expect(screen.getByLabelText("当前目标")).toHaveValue("找耳机"));
}
describe("会话目标与约束", () => {
  it("删除约束发送完整新列表与已读取的版本", async () => {
    vi.spyOn(api, "getConversationContext").mockResolvedValue(initial);
    const update = vi.spyOn(api, "updateConversationContext").mockResolvedValue({ ...initial, revision: 3, context: { goal: "找耳机", constraints: ["有库存"] } });
    render(<ConversationContextEditor conversationId="c-1" />);
    await openEditor();
    fireEvent.change(screen.getByLabelText("当前约束（每行一条）"), { target: { value: "有库存" } });
    fireEvent.click(screen.getByRole("button", { name: "保存目标与约束" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith("c-1", 2, { constraints: ["有库存"] }));
    expect(await screen.findByText(/删除的约束已撤销/)).toBeInTheDocument();
  });
  it("变更目标撤销旧草稿约束，允许填写新的约束", async () => {
    vi.spyOn(api, "getConversationContext").mockResolvedValue(initial);
    const update = vi.spyOn(api, "updateConversationContext").mockResolvedValue({ ...initial, revision: 3, context: { goal: "找键盘", constraints: ["静音"] } });
    render(<ConversationContextEditor conversationId="c-1" />);
    await openEditor();
    fireEvent.change(screen.getByLabelText("当前目标"), { target: { value: "找键盘" } });
    expect(screen.getByLabelText("当前约束（每行一条）")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("当前约束（每行一条）"), { target: { value: "静音" } });
    fireEvent.click(screen.getByRole("button", { name: "保存目标与约束" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith("c-1", 2, { goal: "找键盘", constraints: ["静音"] }));
  });
  it("409 保留草稿，显式重新载入后才可使用新版本提交", async () => {
    const read = vi.spyOn(api, "getConversationContext").mockResolvedValueOnce(initial).mockResolvedValueOnce({ ...initial, revision: 5, context: { goal: "另一个目标", constraints: ["别处修改的条件"] } });
    const update = vi.spyOn(api, "updateConversationContext").mockRejectedValueOnce(new ApiError(409, { detail: "版本冲突" }));
    render(<ConversationContextEditor conversationId="c-1" />);
    await openEditor();
    fireEvent.change(screen.getByLabelText("当前约束（每行一条）"), { target: { value: "我的草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "保存目标与约束" }));
    expect(await screen.findByText(/您的草稿已保留/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存目标与约束" })).toBeDisabled();
    expect(update).toHaveBeenCalledTimes(1);
    expect(read).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "重新载入（保留草稿）" }));
    expect(await screen.findByText(/服务端版本 5/)).toBeInTheDocument();
    expect(screen.getByLabelText("当前约束（每行一条）")).toHaveValue("我的草稿");
    expect(screen.getByRole("button", { name: "保存目标与约束" })).toBeEnabled();
    expect(update).toHaveBeenCalledTimes(1);
    update.mockResolvedValueOnce({ ...initial, revision: 6, context: { goal: "另一个目标", constraints: ["我的草稿"] } });
    fireEvent.click(screen.getByRole("button", { name: "保存目标与约束" }));
    await waitFor(() => expect(update).toHaveBeenLastCalledWith("c-1", 5, { constraints: ["我的草稿"] }));
  });
  it("撤销目标后提示重新明确目标", async () => {
    vi.spyOn(api, "getConversationContext").mockResolvedValue({ ...initial, context: { goal: "", constraints: [], goal_needs_input: true } });
    render(<ConversationContextEditor conversationId="c-1" />);
    const details = screen.getByText("目标与约束").closest("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(await screen.findByText(/原目标已随约束撤销/)).toBeInTheDocument();
  });
});
