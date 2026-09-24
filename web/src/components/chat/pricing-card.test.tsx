import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatPricingCard } from "./pricing-card";

describe("ChatPricingCard", () => {
  it("根据 optimize_cart 的字段渲染优惠瀑布", () => {
    render(
      <ChatPricingCard
        data={{
          original_total: 349.98,
          savings: [
            { type: "coupon", code: "SAVE10", amount: 35 },
            { type: "loyalty_discount", tier: "gold", amount: 10.5 },
          ],
          total_savings: 45.5,
          final_total: 304.48,
          savings_percentage: 13,
        }}
      />
    );
    expect(screen.getByText("优惠明细")).toBeInTheDocument();
    expect(screen.getByText("优惠券 SAVE10")).toBeInTheDocument();
    expect(screen.getByText("gold 会员折扣")).toBeInTheDocument();
    expect(screen.getByText("¥304.48")).toBeInTheDocument();
    expect(screen.getByText(/已省 13%/)).toBeInTheDocument();
  });

  it("根据 get_active_deals 的字段渲染进行中的优惠，且不显示瀑布", () => {
    render(
      <ChatPricingCard
        data={{
          coupons: [{ code: "WELCOME15", description: "New customer discount", discount_type: "percentage", discount_value: 15 }],
          promotions: [{ name: "Summer Sale", type: "flash_sale", end_date: "2026-09-01" }],
        }}
      />
    );
    expect(screen.getByText("优惠与促销")).toBeInTheDocument();
    expect(screen.getByText("WELCOME15")).toBeInTheDocument();
    expect(screen.getByText("Summer Sale")).toBeInTheDocument();
    expect(screen.queryByText("优惠明细")).not.toBeInTheDocument();
  });

  it("代码块实际为空时不渲染任何内容（第 8.4 阶段 Step 4c 实测发现）", () => {
    // 实测发现的真实缺陷：optimize_cart 无法解析购物车（未提供商品），
    // 而模型仍然输出了一段 `pricing` 代码块且字段全空——于是渲染出一个
    // 标题栏下方却是空白内容的卡片。
    const { container } = render(<ChatPricingCard data={{}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("组合优惠与限时秒杀的优惠行标签正确", () => {
    render(
      <ChatPricingCard
        data={{
          original_total: 100,
          savings: [
            { type: "bundle_promotion", name: "Headphone + Case Bundle", amount: 20 },
            { type: "flash_sale", name: "Flash Friday", product: "Sony WH-1000XM5", amount: 15 },
          ],
        }}
      />
    );
    expect(screen.getByText("Headphone + Case Bundle")).toBeInTheDocument();
    expect(screen.getByText("Flash Friday（Sony WH-1000XM5）")).toBeInTheDocument();
  });
});
