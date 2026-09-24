import { describe, expect, it } from "vitest";
import { visibleGroups, labelForPath } from "./nav";

describe("visibleGroups", () => {
  it("普通顾客可见「购物」+「账户」，隐藏管理/商家项", () => {
    const groups = visibleGroups({ isAdmin: false, isSeller: false });
    const labels = groups.flatMap((g) => g.items.map((i) => i.label));
    expect(labels).toContain("首页");
    expect(labels).toContain("对话");
    expect(labels).toContain("智能体"); // 所有已登录用户可见
    expect(labels).toContain("运行记录");   // 第 4 阶段——所有用户可见
    expect(labels).toContain("个人中心");
    expect(labels).not.toContain("用量统计"); // adminOnly
    expect(labels).not.toContain("商家"); // sellerOnly
    expect(labels).not.toContain("Marketplace");
    expect(groups.find((g) => g.label === "管理")).toBeUndefined();
  });

  it("商家可见商家项，但看不到管理项", () => {
    const groups = visibleGroups({ isAdmin: false, isSeller: true });
    const labels = groups.flatMap((g) => g.items.map((i) => i.label));
    expect(labels).toContain("商家");
    expect(labels).not.toContain("用量统计");
  });

  it("管理员可见管理项（概览、审批、用量统计）——没有旧标签", () => {
    const groups = visibleGroups({ isAdmin: true, isSeller: true });
    const labels = groups.flatMap((g) => g.items.map((i) => i.label));
    expect(labels).toContain("概览");
    expect(labels).toContain("审批");
    expect(labels).toContain("用量统计");
    expect(labels).toContain("运行记录"); // 只有一个「运行记录」入口，来自「购物」分组
    expect(labels).toContain("商家");
    expect(labels).not.toContain("Requests");
    expect(labels).not.toContain("Audit");
    // 现在只有一个「运行记录」入口（/admin/audit 的重复项已被移除）
    expect(labels.filter((l) => l === "运行记录")).toHaveLength(1);
  });
});

describe("labelForPath", () => {
  it("匹配最具体的导航项", () => {
    expect(labelForPath("/admin/usage")).toBe("用量统计");
    expect(labelForPath("/admin")).toBe("概览");
    expect(labelForPath("/runs")).toBe("运行记录"); // 唯一的面向用户运行记录页
  });

  it("把嵌套的详情路由匹配到其父级项", () => {
    expect(labelForPath("/orders/abc-123")).toBe("订单");
    expect(labelForPath("/products/p1")).toBe("商品");
    expect(labelForPath("/agents/product-discovery")).toBe("智能体");
  });

  it("未知路径兜底到首页", () => {
    expect(labelForPath("/totally-unknown")).toBe("首页");
  });
});
