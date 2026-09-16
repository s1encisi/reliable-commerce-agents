import { webcrypto } from "node:crypto";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { clearReturnIntent, currentReturnIntent, getReturnIntent } from "./return-intent";
import { returnOperationSchema } from "./return-operation";

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("crypto", webcrypto);
});
afterEach(() => vi.unstubAllGlobals());

describe("return operation identity", () => {
  it("keeps the same ID across retries without persisting the reason", async () => {
    const first = await getReturnIntent("owner", "order", " Sensitive reason ", "store_credit");
    expect(await getReturnIntent("owner", "order", "Sensitive reason", "store_credit")).toBe(first);
    expect(currentReturnIntent("owner", "order")).toBe(first);
    expect(localStorage.getItem("return-intent:v1:owner:order")).not.toContain("Sensitive reason");
  });
  it("starts a new intent when parameters change or the user explicitly resets", async () => {
    const first = await getReturnIntent("owner", "order", "reason", "store_credit");
    const changed = await getReturnIntent("owner", "order", "new reason", "store_credit");
    expect(changed).not.toBe(first);
    clearReturnIntent("owner", "order");
    expect(await getReturnIntent("owner", "order", "new reason", "store_credit")).not.toBe(changed);
  });
  it("separates owners and rejects malformed success payloads", async () => {
    await getReturnIntent("one", "order", "reason", "store_credit");
    expect(currentReturnIntent("two", "order")).toBeNull();
    expect(returnOperationSchema.safeParse({ return_id: "not-an-id", refund_amount: "wrong" }).success).toBe(false);
  });
});
