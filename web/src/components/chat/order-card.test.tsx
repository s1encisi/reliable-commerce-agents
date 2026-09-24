import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatOrderCard } from "./order-card";

describe("ChatOrderCard", () => {
  it("对象形式的 shipping_address 能正常渲染而不会崩溃（第 8.2 阶段）", () => {
    // 数据库的原生形态是 JSONB——chat-schemas.ts 的 OrderDataSchema 接受
    // 这种联合类型，但组件此前把 shipping_address 声明为仅字符串并直接
    // 作为子节点渲染，一旦智能体输出未经字符串化的对象形式，就会抛出
    // “Objects are not valid as a React child”。
    const data = {
      id: "48bfb7a1-0b02-4c89-94c9-552d629aaa92",
      status: "shipped",
      shipping_address: { street: "123 Main St", city: "Springfield", state: "IL", zip: "62704" },
    };
    render(<ChatOrderCard data={data} />);
    expect(screen.getByText("123 Main St, Springfield, IL, 62704")).toBeInTheDocument();
  });

  it("字符串形式的 shipping_address 仍能正常渲染", () => {
    const data = { id: "order-1", shipping_address: "456 Oak Ave, Metropolis" };
    render(<ChatOrderCard data={data} />);
    expect(screen.getByText("456 Oak Ave, Metropolis")).toBeInTheDocument();
  });

  it("shipping_address 为 null 时不渲染地址行", () => {
    const data = { id: "order-1", shipping_address: null as unknown as undefined };
    render(<ChatOrderCard data={data} />);
    expect(screen.queryByText(/Main St/)).not.toBeInTheDocument();
  });

  it("商品缺少 unit_price 或 quantity 时不渲染 NaN", () => {
    const data = {
      id: "order-1",
      items: [{ name: "Mystery Item" }],
    };
    render(<ChatOrderCard data={data} />);
    expect(screen.getByText("Mystery Item")).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });

  it("缺少 status 时回退到时间线事件的 label", () => {
    const data = {
      id: "order-1",
      timeline: [{ label: "Order placed", date: "2026-01-01" }],
    };
    render(<ChatOrderCard data={data} />);
    expect(screen.getByText("Order placed")).toBeInTheDocument();
  });

  describe("退货按钮的可用性判断（第 10 阶段 / 问题 #8）", () => {
    it("已送达订单在缺少可用性数据时显示「退货」（默认：可退货）", () => {
      const data = { id: "order-1", status: "delivered" };
      render(<ChatOrderCard data={data} />);
      expect(screen.getByRole("button", { name: /退货/ })).toBeInTheDocument();
    });

    it("return_eligible 显式为 false 时隐藏「退货」，即使订单已送达", () => {
      const data = { id: "order-1", status: "delivered", return_eligible: false };
      render(<ChatOrderCard data={data} />);
      expect(screen.queryByRole("button", { name: /退货/ })).not.toBeInTheDocument();
    });

    it("return_eligible 显式为 true 时显示「退货」", () => {
      const data = { id: "order-1", status: "delivered", return_eligible: true };
      render(<ChatOrderCard data={data} />);
      expect(screen.getByRole("button", { name: /退货/ })).toBeInTheDocument();
    });

    it("送达时间线日期超出 30 天窗口时隐藏「退货」", () => {
      const deliveredAt = new Date();
      deliveredAt.setDate(deliveredAt.getDate() - 70);
      const data = {
        id: "order-1",
        status: "delivered",
        timeline: [{ status: "delivered", date: deliveredAt.toISOString() }],
      };
      render(<ChatOrderCard data={data} />);
      expect(screen.queryByRole("button", { name: /退货/ })).not.toBeInTheDocument();
    });

    it("送达时间线日期在 30 天窗口内时显示「退货」", () => {
      const deliveredAt = new Date();
      deliveredAt.setDate(deliveredAt.getDate() - 5);
      const data = {
        id: "order-1",
        status: "delivered",
        timeline: [{ status: "delivered", date: deliveredAt.toISOString() }],
      };
      render(<ChatOrderCard data={data} />);
      expect(screen.getByRole("button", { name: /退货/ })).toBeInTheDocument();
    });

    it("非已送达订单无论 return_eligible 如何都不显示「退货」", () => {
      const data = { id: "order-1", status: "shipped", return_eligible: true };
      render(<ChatOrderCard data={data} />);
      expect(screen.queryByRole("button", { name: /退货/ })).not.toBeInTheDocument();
    });
  });
});
