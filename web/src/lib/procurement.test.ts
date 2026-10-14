import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { buildProcurementSpec, planLayers, procurementApi, procurementGoal, reconcileProcurement, streamProcurement, type ProcurementTask } from "./procurement";

const valid = { query: "办公耳机", quantity: "3", alternatives: "2", budget: "1200.5", rating: "4", preferences: "通勤\n佩戴舒适" };
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("采购控制契约", () => {
  it("金额保持十进制字符串并只提交已知Spec字段", () => {
    const spec = buildProcurementSpec({ ...valid, _parse: { unsupported_conditions: ["包税"] } } as typeof valid);
    expect(spec).toEqual({ query: "办公耳机", quantity: 3, alternative_count: 2, total_budget: "1200.50", currency: "CNY", in_stock: true, min_rating: 4, preferences: ["通勤", "佩戴舒适"], budget_scope: "goods", quote_policy: "single_coupon_v1" });
    expect(spec).not.toHaveProperty("_parse");
  });
  it.each([{ quantity: "" }, { quantity: "1.5" }, { quantity: "101" }, { budget: "0" }, { budget: "0.001" }, { budget: "1e3" }, { alternatives: "3" }, { rating: "6" }])("不默认补全无效需求 %j", (invalid) => expect(buildProcurementSpec({ ...valid, ...invalid })).toBeNull());
  it("改预算后的goal和结构化spec一致", () => {
    const spec = buildProcurementSpec(valid)!;
    expect(procurementGoal(spec)).toContain("总货款预算 1200.50 元");
    expect(procurementGoal(spec)).toContain("不含运费和税费");
  });
  it("较低revision或同revision较低seq不能覆盖新状态，也不混入其他任务", () => {
    const current = { id: "a", revision: 4, state_seq: 20 } as ProcurementTask;
    expect(reconcileProcurement(current, { ...current, revision: 3, state_seq: 100 })).toBe(current);
    expect(reconcileProcurement(current, { ...current, state_seq: 19 })).toBe(current);
    expect(reconcileProcurement(current, { ...current, id: "b", revision: 5 })).toBe(current);
    expect(reconcileProcurement(current, { ...current, state_seq: 21 }).state_seq).toBe(21);
  });
  it("按依赖分层，而不是把无关节点排成手动线性流程", () => {
    const step = (id: string, depends_on: string[]) => ({ id, depends_on, capability: "check_inventory", parameters: {}, instruction: "" });
    expect(planLayers([step("stock", ["search"]), step("quote", ["search"]), step("search", [])]).map((layer) => layer.map((node) => node.id))).toEqual([["search"], ["stock", "quote"]]);
  });
});

describe("只读SSE续接", () => {
  it("处理分块CRLF、重复序号、多行data且发送续接游标", async () => {
    const event = (seq: number, task_id = "a") => ({ seq, event: "step.finished", created_at: "2026-10-14T00:00:00Z", payload: { task_id, status: "running", revision: 2, spec_revision: 1, plan_revision: 1 } });
    const content = ["id: 4\r\ndata: " + JSON.stringify(event(4)) + "\r\n\r\n", "data: " + JSON.stringify(event(5)) + "\n\n", "data: " + JSON.stringify(event(5)) + "\n\n", "data: " + JSON.stringify(event(6, "other")) + "\n\n", "data: " + JSON.stringify(event(7)).replace(',"payload"', ',\ndata: "payload"') + "\n\n"].join("");
    const bytes = new TextEncoder().encode(content);
    const fetchMock = vi.fn().mockResolvedValue(new Response(new ReadableStream({ start(controller) { for (let i = 0; i < bytes.length; i += 13) controller.enqueue(bytes.slice(i, i + 13)); controller.close(); } })));
    vi.stubGlobal("fetch", fetchMock); vi.spyOn(api, "getToken").mockReturnValue("synthetic-token");
    const received: number[] = [];
    await streamProcurement("a", 4, new AbortController().signal, (value) => received.push(value.seq));
    expect(received).toEqual([5, 7]);
    expect(fetchMock).toHaveBeenCalledWith("/api/tasks/a/events?after_seq=4", expect.objectContaining({ headers: expect.objectContaining({ "Last-Event-ID": "4", Authorization: "Bearer synthetic-token" }) }));
  });
  it("401仅使用GET刷新认证，不重放任何控制请求", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(null, { status: 401 })).mockResolvedValueOnce(new Response(": ready\n\n"));
    vi.stubGlobal("fetch", fetchMock); vi.spyOn(procurementApi, "get").mockResolvedValue({} as ProcurementTask);
    const start = vi.spyOn(procurementApi, "start"), confirm = vi.spyOn(procurementApi, "confirm");
    await streamProcurement("a", 9, new AbortController().signal, vi.fn());
    expect(procurementApi.get).toHaveBeenCalledWith("a"); expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(start).not.toHaveBeenCalled(); expect(confirm).not.toHaveBeenCalled();
  });
});
