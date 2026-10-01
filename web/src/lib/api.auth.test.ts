import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => { vi.unstubAllGlobals(); api.setToken(null); api.setRefreshToken(null); localStorage.clear(); });

describe("认证失败语义", () => {
  it("登录密码错误不刷新令牌、不清除已有状态且显示凭据错误", async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 401, ok: false, json: async () => ({ detail: "Invalid email or password" }) });
    vi.stubGlobal("fetch", fetcher);
    api.setRefreshToken("existing-refresh");
    localStorage.setItem("ecommerce_user", "existing");
    await expect(api.login("x@test", "wrong")).rejects.toThrow("邮箱或密码不正确");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("ecommerce_user")).toBe("existing");
  });
  it("受保护接口 401 仍刷新并重放一次", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce({ status: 401, ok: false })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ access_token: "fresh" }) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ orders: [], total: 0 }) });
    vi.stubGlobal("fetch", fetcher);
    api.setRefreshToken("refresh");
    await expect(api.getOrders()).resolves.toEqual({ orders: [], total: 0 });
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls[1][0]).toBe("/api/auth/refresh");
  });
});
