import { webcrypto } from "node:crypto";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { clearReturnIntent, currentReturnIntent, getReturnIntent } from "./return-intent";
import { returnOperationSchema } from "./return-operation";

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("crypto", webcrypto);
});
afterEach(() => vi.unstubAllGlobals());

describe("退货操作标识", () => {
  it("重试之间保持同一个 ID，且不持久化退货原因", async () => {
    const first = await getReturnIntent("owner", "order", " Sensitive reason ", "store_credit");
    expect(await getReturnIntent("owner", "order", "Sensitive reason", "store_credit")).toBe(first);
    expect(currentReturnIntent("owner", "order")).toBe(first);
    expect(localStorage.getItem("return-intent:v1:owner:order")).not.toContain("Sensitive reason");
  });
  it("参数变化或用户显式重置时开启新的意向", async () => {
    const first = await getReturnIntent("owner", "order", "reason", "store_credit");
    const changed = await getReturnIntent("owner", "order", "new reason", "store_credit");
    expect(changed).not.toBe(first);
    clearReturnIntent("owner", "order");
    expect(await getReturnIntent("owner", "order", "new reason", "store_credit")).not.toBe(changed);
  });
  it("按 owner 隔离，并拒绝格式错误的成功响应", async () => {
    await getReturnIntent("one", "order", "reason", "store_credit");
    expect(currentReturnIntent("two", "order")).toBeNull();
    expect(returnOperationSchema.safeParse({ return_id: "not-an-id", refund_amount: "wrong" }).success).toBe(false);
  });
});
