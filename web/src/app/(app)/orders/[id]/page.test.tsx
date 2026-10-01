import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import OrderDetailPage from "./page";
import { api } from "@/lib/api";

const state = vi.hoisted(() => ({ user: { email: "customer@example.test", role: "customer" }, params: { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1" }, push: vi.fn() }));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ user: state.user, isLoading: false }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: state.push }), useParams: () => state.params, useSearchParams: () => new URLSearchParams() }));
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

describe("订单申请状态语义", () => {
  it("顶部与历史只显示已申请退货，并保留原因原文", async () => {
    localStorage.clear();
    const reason = "视频演示：商品外壳破损 / Damaged shell";
    vi.spyOn(api, "getOrder").mockResolvedValue({
      id: state.params.id, date: "2026-09-01", status: "returned", items: [],
      total: 79, discount: 0, shipping_address: { street: "合成街道", city: "合成城市", state: "", zip: "" },
      status_history: [{ status: "returned", timestamp: "2026-10-01T00:00:00Z", notes: `Return initiated: ${reason}` }],
      return: { id: "return-1", reason, status: "requested", refund_method: "original_payment", refund_amount: 79 },
    });
    render(<OrderDetailPage />);
    expect(await screen.findAllByText("已申请退货", { exact: true })).toHaveLength(2);
    expect(screen.getByText(`已发起退货申请：${reason}`, { exact: true })).toBeInTheDocument();
    expect(screen.getByText("已创建申请", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText("已退货", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText(`Return initiated: ${reason}`, { exact: true })).not.toBeInTheDocument();
  });
});
