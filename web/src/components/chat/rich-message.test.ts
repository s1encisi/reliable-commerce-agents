import { describe, expect, it } from "vitest";
import { parseContent, type Segment } from "./rich-message";

const PRODUCT = {
  name: "Sony WH-1000XM5",
  id: "74427b99-8717-4481-8c00-d05dd19b120f",
  price: 299.99,
  original_price: 349.99,
  rating: 4.7,
  review_count: 15,
  category: "Electronics",
  brand: "Sony",
  description: "Premium wireless noise-cancelling headphones.",
};

const PRODUCT_2 = {
  name: "AirPods Max",
  id: "7cf9d9fe-e75a-4e49-b1fb-12bc92d4b1e4",
  price: 449.99,
  rating: 4.5,
  category: "Electronics",
  brand: "Apple",
};

const cardTypes = (segs: Segment[]) => segs.filter((s) => s.type !== "text").map((s) => s.type);
const textOf = (segs: Segment[]) => segs.filter((s) => s.type === "text").map((s) => s.text).join(" ");

describe("parseContent —— 卡片代码块解析", () => {
  it("能解析带换行的规范 product 围栏块", () => {
    const content = "给你挑了一个：\n```product\n" + JSON.stringify(PRODUCT) + "\n```\n还要看别的吗？";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["product"]);
    const card = segs.find((s) => s.type === "product");
    expect(card?.data?.name).toBe("Sony WH-1000XM5");
  });

  it("能解析传输层丢掉换行后的塌缩围栏块", () => {
    // ```product{...}``` —— 标记后没有换行（真实存在的缺陷）
    const content = "详情见下：```product" + JSON.stringify(PRODUCT) + "```还要看更多吗？";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["product"]);
    // 原始 JSON 绝不能泄漏到文本片段中
    expect(textOf(segs)).not.toContain('"id"');
    expect(textOf(segs)).not.toContain("product{");
  });

  it("同一商品同时以规范与塌缩两种形式到达时会去重", () => {
    const content =
      "看一下：```product" + JSON.stringify(PRODUCT) + "```" +
      "你好！它又出现了一次：\n```product\n" + JSON.stringify(PRODUCT) + "\n```\n谢谢";
    const segs = parseContent(content);
    // 两个代码块、同一个 id → 只应生成一张卡片
    expect(cardTypes(segs)).toEqual(["product"]);
  });

  it("两件商品的数组会渲染为单张对比卡片", () => {
    const content = "对比如下：\n```products\n" + JSON.stringify([PRODUCT, PRODUCT_2]) + "\n```";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["comparison"]);
  });

  it("3 件及以上商品会渲染为各自独立的商品卡片", () => {
    const content = "```products\n" + JSON.stringify([PRODUCT, PRODUCT_2, { ...PRODUCT, id: "x" }]) + "\n```";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["product", "product", "product"]);
  });

  it("不会把非卡片类型的围栏块误读成卡片", () => {
    const content = "```python\nprint('product')\n```";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual([]);
  });

  it("不会匹配 ```product-ideas（正文未以 { 或 [ 开头）", () => {
    const content = "```product-ideas\nsome list\n```";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual([]);
  });

  it("没有卡片时原样返回纯文本", () => {
    const content = "这是一个没有任何结构化数据的普通回答。";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual([]);
    expect(textOf(segs)).toContain("普通回答");
  });

  it("能解析塌缩的 order 围栏块", () => {
    const order = { id: "48bfb7a1-0b02-4c89-94c9-552d629aaa92", status: "shipped", total: 299.99, carrier: "顺丰速运", tracking: "SF2773037221" };
    const content = "您的订单：```order" + JSON.stringify(order) + "```还有别的事吗？";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["order"]);
  });

  it("即使字段为 null（shipping_address: null）也能渲染结算卡片", () => {
    const checkout = {
      message: "您的购物车已就绪！",
      item_count: 1,
      subtotal: 45.49,
      discount: 0,
      total: 45.49,
      items: [{ name: "无线降噪耳机", brand: "声阔", quantity: 1, unit_price: 45.49, subtotal: 45.49 }],
      shipping_address: null, // 后端发的是 null 而不是 undefined —— 不能因此丢掉整张卡片
      address_ready: false,
    };
    const content = "您的购物车：\n```checkout\n" + JSON.stringify(checkout) + "\n```\n要继续吗？";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["checkout"]);
    // 原始 JSON 绝不能作为文本泄漏
    expect(textOf(segs)).not.toContain("address_ready");
  });

  it("编排器重复复述的同一份结算信息会去重", () => {
    const checkout = { message: "您的购物车已就绪！", item_count: 1, total: 45.49, items: [{ name: "机械键盘", unit_price: 45.49, quantity: 1 }], shipping_address: null };
    const content =
      "在这里：```checkout" + JSON.stringify(checkout) + "```" +
      "你好！再说一次：\n```checkout\n" + JSON.stringify(checkout) + "\n```\n要继续吗？";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["checkout"]);
  });

  it("能解析 sentiment 围栏块（第 8.4 阶段 Step 4a）", () => {
    const sentiment = {
      product_id: "74427b99-8717-4481-8c00-d05dd19b120f",
      product_name: "Sony WH-1000XM5",
      overall_sentiment: "positive",
      average_rating: 4.7,
      total_reviews: 15,
      rating_distribution: { "5": 10, "4": 3, "3": 1, "2": 1, "1": 0 },
      pros: ["质量好"],
      cons: ["偏贵"],
    };
    const content = "这是情感拆解：\n```sentiment\n" + JSON.stringify(sentiment) + "\n```\n还要看别的吗？";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["sentiment"]);
    expect(segs.find((s) => s.type === "sentiment")?.data?.overall_sentiment).toBe("positive");
  });

  it("能解析 inventory 围栏块（第 8.4 阶段 Step 4b）", () => {
    const inventory = {
      product_id: "74427b99-8717-4481-8c00-d05dd19b120f",
      product_name: "Sony WH-1000XM5",
      in_stock: true,
      total_quantity: 42,
      warehouses: [{ warehouse: "东部仓", region: "east", quantity: 30, low_stock: false }],
    };
    const content = "这是库存情况：\n```inventory\n" + JSON.stringify(inventory) + "\n```\n还要看别的吗？";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["inventory"]);
    expect(segs.find((s) => s.type === "inventory")?.data?.in_stock).toBe(true);
  });

  it("能解析 pricing 围栏块（第 8.4 阶段 Step 4c）", () => {
    const pricing = {
      original_total: 349.98,
      savings: [{ type: "coupon", code: "SAVE10", amount: 35 }],
      total_savings: 35,
      final_total: 314.98,
    };
    const content = "这是您的优惠明细：\n```pricing\n" + JSON.stringify(pricing) + "\n```\n还要看别的吗？";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["pricing"]);
    expect(segs.find((s) => s.type === "pricing")?.data?.final_total).toBe(314.98);
  });

  it("能解析 return 围栏块 —— 此前唯一没有覆盖的卡片类型", () => {
    const ret = {
      order_id: "48bfb7a1-0b02-4c89-94c9-552d629aaa92",
      return_id: "9a1c1e2a-2222-4c89-94c9-552d629aaa92",
      status: "approved",
      return_label_url: "/api/returns/abc123/label",
      refund_amount: 79.99,
      refund_method: "original_payment",
      refund_timeline: "5-7 个工作日",
    };
    const content = "您的退货已受理：\n```return\n" + JSON.stringify(ret) + "\n```\n还要看别的吗？";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["return"]);
    expect(segs.find((s) => s.type === "return")?.data?.refund_amount).toBe(79.99);
  });
});

describe("parseContent —— 绝不渲染原始 JSON（第 8.2 阶段）", () => {
  it("模式校验失败的围栏块会被丢弃，而不是展示原始 JSON", () => {
    // 按 ReturnDataSchema，refund_amount 必须是非负数 ——
    // 负值会导致 validateCard 失败。
    const invalidReturn = { order_id: "o-1", refund_amount: -50 };
    const content = "前文。\n```return\n" + JSON.stringify(invalidReturn) + "\n```\n后文。";
    const segs = parseContent(content);

    expect(cardTypes(segs)).toEqual([]);
    expect(textOf(segs)).not.toContain("refund_amount");
    expect(textOf(segs)).not.toContain("```");
    // 周围的对话文本应保留 —— 只丢弃那个坏的围栏块。
    expect(textOf(segs)).toContain("前文。");
    expect(textOf(segs)).toContain("后文。");
  });

  it("已识别标记但 JSON 格式错误的围栏块会被丢弃，而不是展示原始文本", () => {
    const content = "前文。\n```order\n{not valid json at all\n```\n后文。";
    const segs = parseContent(content);

    expect(cardTypes(segs)).toEqual([]);
    expect(textOf(segs)).not.toContain("not valid json");
    expect(textOf(segs)).toContain("前文。");
    expect(textOf(segs)).toContain("后文。");
  });

  it("未识别的、形似 JSON 的围栏标记会被丢弃，而不是落到原始代码块分支", () => {
    // 模拟 LLM 幻觉出一个本解析器从未学过的标记，或后端先于本文件
    // 引入了新的卡片类型 —— 这是通用守卫，而非上面针对 5 种标记的专用守卫。
    const content = '前文。\n```cart\n{"items": ["a", "b"], "total": 12.5}\n```\n后文。';
    const segs = parseContent(content);

    expect(cardTypes(segs)).toEqual([]);
    expect(textOf(segs)).not.toContain("items");
    expect(textOf(segs)).not.toContain("```");
    expect(textOf(segs)).toContain("前文。");
    expect(textOf(segs)).toContain("后文。");
  });
});
