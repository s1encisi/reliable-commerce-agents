import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatInventoryCard } from "./inventory-card";

describe("ChatInventoryCard", () => {
  it("仅凭 check_stock 的字段也能渲染", () => {
    render(
      <ChatInventoryCard
        data={{
          product_name: "Sony WH-1000XM5",
          in_stock: true,
          total_quantity: 42,
          warehouses: [
            { warehouse: "东部仓", region: "east", quantity: 30, low_stock: false },
            { warehouse: "西部仓", region: "west", quantity: 12, low_stock: true },
          ],
        }}
      />
    );
    expect(screen.getByText("Sony WH-1000XM5")).toBeInTheDocument();
    expect(screen.getByText("有货")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("东部仓")).toBeInTheDocument();
    expect(screen.getByText(/12（库存偏低）/)).toBeInTheDocument();
  });

  it("缺货时使用 destructive 色调", () => {
    render(<ChatInventoryCard data={{ product_name: "某商品", in_stock: false, total_quantity: 0 }} />);
    expect(screen.getByText("缺货").className).toContain("text-destructive");
  });

  it("同时调用过 get_restock_schedule 时展示即将补货", () => {
    render(
      <ChatInventoryCard
        data={{
          product_name: "某商品",
          upcoming_restocks: [{ warehouse: "东部仓", expected_quantity: 100, expected_date: "2026-09-01" }],
        }}
      />
    );
    expect(screen.getByText("即将补货")).toBeInTheDocument();
    expect(screen.getByText("2026-09-01")).toBeInTheDocument();
  });

  it("只给出商品名称时以精简形式渲染", () => {
    render(<ChatInventoryCard data={{ product_name: "某商品" }} />);
    expect(screen.getByText("某商品")).toBeInTheDocument();
  });

  describe("配送方式（第 8.4 阶段 Step 5 —— 可交互）", () => {
    const shippingData = {
      product_name: "Sony WH-1000XM5",
      ships_from: { warehouse: "东部仓", region: "east", quantity_available: 30 },
      shipping_options: [
        { carrier: "中通快递", speed_tier: "标准", price: 5.99, delivery_window: "5-7 个工作日" },
        { carrier: "顺丰速运", speed_tier: "加急", price: 24.99, delivery_window: "1 个工作日" },
      ],
    };

    it("为每个选项展示承运商、价格与送达时间", () => {
      render(<ChatInventoryCard data={shippingData} onAction={() => {}} />);
      expect(screen.getByText(/发货仓库：东部仓/)).toBeInTheDocument();
      expect(screen.getByText("中通快递", { exact: false })).toBeInTheDocument();
      expect(screen.getByText("¥5.99")).toBeInTheDocument();
      expect(screen.getByText("5-7 个工作日")).toBeInTheDocument();
      expect(screen.getByText("¥24.99")).toBeInTheDocument();
    });

    it("点击「选择」时以自然语言确认消息调用 onAction", async () => {
      const onAction = vi.fn();
      render(<ChatInventoryCard data={shippingData} onAction={onAction} />);
      const selectButtons = screen.getAllByRole("button", { name: "选择" });
      await userEvent.click(selectButtons[1]);
      expect(onAction).toHaveBeenCalledWith(
        "我选择顺丰速运（加急）配送，运费 ¥24.99，1 个工作日。"
      );
    });

    it("未提供 onAction 时不显示「选择」按钮（只读渲染）", () => {
      render(<ChatInventoryCard data={shippingData} />);
      expect(screen.queryByRole("button", { name: "选择" })).not.toBeInTheDocument();
    });
  });
});
