import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
afterEach(() => vi.unstubAllGlobals());
describe("任务版本化请求", () => {
  it("PATCH 保留完整目标约束且各动作发送 revision", async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    vi.stubGlobal("fetch", fetcher);
    await api.updateTask("task-1", 7, { goal: "目标", constraints: ["文字约束"], requirements: { max_price: 100 } });
    expect(fetcher.mock.calls[0][0]).toBe("/api/tasks/task-1");
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ revision: 7, goal: "目标", constraints: ["文字约束"], requirements: { max_price: 100 } });
    await api.planTask("task-1", 8);
    await api.nextTaskStep("task-1", 10);
    await api.recoverTask("task-1", 11);
    await api.confirmTask("task-1", 12);
    expect(fetcher.mock.calls.slice(1).map(([url, init]) => [url, JSON.parse(init.body)])).toEqual([
      ["/api/tasks/task-1/plan", { revision: 8 }], ["/api/tasks/task-1/next", { revision: 10 }],
      ["/api/tasks/task-1/recover", { revision: 11 }], ["/api/tasks/task-1/confirm", { revision: 12, confirmed: true }],
    ]);
  });
});
