import { describe, expect, it } from "vitest";
import { cardKindsIn, deriveSuggestions, fromClosingQuestion } from "./suggestions";

const FALLBACK = [
  { label: "Fallback one", prompt: "one" },
  { label: "Fallback two", prompt: "two" },
  { label: "Fallback three", prompt: "three" },
];

const productFence = (n = 1) =>
  Array.from({ length: n }, () => '```product\n{"id":"x","name":"Thing"}\n```').join("\n\n");

describe("cardKindsIn", () => {
  it("按顺序找出每个代码块，并保留重复项", () => {
    const text = `Here you go\n${productFence()}\nand the order:\n\`\`\`order\n{}\n\`\`\``;
    expect(cardKindsIn(text)).toEqual(["product", "order"]);
  });

  it("对重复项计数而不是合并", () => {
    // 两件商品才值得「对比这些商品」，一件只值得「查看库存」。数量本身
    // 就是信号，因此在这里去重会把它丢掉。
    expect(cardKindsIn(productFence(2))).toEqual(["product", "product"]);
  });

  it("普通散文里找不到任何代码块", () => {
    expect(cardKindsIn("We are open until 6pm.")).toEqual([]);
  });
});

describe("fromClosingQuestion", () => {
  it("把二选一的问句拆成胶囊", () => {
    expect(
      fromClosingQuestion("Sure. Would you like to see more options or compare with other models?")
    ).toEqual([
      { label: "See more options", prompt: "See more options" },
      { label: "Compare with other models", prompt: "Compare with other models" },
    ]);
  });

  it("拆分逗号分隔的列表", () => {
    const out = fromClosingQuestion("Would you like to check stock, see reviews, or compare prices?");
    expect(out.map((s) => s.label)).toEqual(["Check stock", "See reviews", "Compare prices"]);
  });

  it("忽略不以问句结尾的消息", () => {
    // 回答中段的散文不是提议，把它当成提议正是这一层会产出用户根本没要的
    // 胶囊的原因。
    expect(fromClosingQuestion("Would you like tea or coffee? Anyway, here is your order.")).toEqual([]);
  });

  it("只产出单个候选时丢弃整个层级", () => {
    // 一个真实胶囊配两个固定胶囊，读起来比三个固定胶囊更糟，因此整层丢弃
    // 而不是凑数。
    expect(fromClosingQuestion("Would you like me to place the order?")).toEqual([]);
  });

  it("拒绝长到不适合做胶囊的候选", () => {
    const long = "a".repeat(60);
    expect(fromClosingQuestion(`Would you like ${long} or ${long}?`)).toEqual([]);
  });

  it("拒绝解析残留物", () => {
    // 漏进最后一句里的代码块绝不能变成胶囊。
    expect(fromClosingQuestion('Would you like {"id":"x"} or ```product?')).toEqual([]);
  });

  it("首字母大写并去掉末尾标点", () => {
    const out = fromClosingQuestion("Do you want me to track it, or cancel it?");
    expect(out.map((s) => s.label)).toEqual(["Track it", "Cancel it"]);
  });

  it("识别以全角问号结尾的中文问句", () => {
    // 界面已经是中文的：只认半角 `?` 会让整个「结尾提问」层级静默失效。
    expect(fromClosingQuestion("您想查看库存，还是对比一下价格？")).toEqual([
      { label: "查看库存", prompt: "查看库存" },
      { label: "对比一下价格", prompt: "对比一下价格" },
    ]);
  });

  it("剥掉中文疑问前缀，只把候选本身做成胶囊", () => {
    const out = fromClosingQuestion("要不要我帮您跟踪这笔订单，或者发起退货？");
    expect(out.map((s) => s.label)).toEqual(["帮您跟踪这笔订单", "发起退货"]);
  });

  it("中文问句但以句号结尾时仍不产出胶囊", () => {
    expect(fromClosingQuestion("您想查看库存，还是对比一下价格。")).toEqual([]);
  });

  it("英文问句的行为保持不变", () => {
    expect(fromClosingQuestion("Would you like to track it or return it?")).toEqual([
      { label: "Track it", prompt: "Track it" },
      { label: "Return it", prompt: "Return it" },
    ]);
  });
});

describe("deriveSuggestions", () => {
  it("没有助手消息时使用兜底项", () => {
    // 空对话场景必须与当前表现完全一致。
    expect(deriveSuggestions(undefined, FALLBACK)).toEqual(FALLBACK);
  });

  it("散文既无结构化负载也无收尾问句时使用兜底项", () => {
    expect(deriveSuggestions("We're open until 6pm.", FALLBACK)).toEqual(FALLBACK);
  });

  it("结构化负载优先于散文", () => {
    // 两个信号同时存在。卡片是更强的那个——它才是用户正在看的内容，且
    // 不依赖模型的措辞。
    const text = `${productFence()}\n\nWould you like tea or coffee?`;
    expect(deriveSuggestions(text, FALLBACK).map((s) => s.label)).toEqual([
      "查看库存",
      "查看评论",
      "找相似商品",
    ]);
  });

  it("展示多件商品时切换为对比胶囊", () => {
    expect(deriveSuggestions(productFence(2), FALLBACK).map((s) => s.label)).toEqual([
      "对比这些商品",
      "查看库存",
      "有优惠吗？",
    ]);
  });

  it("products 数组按定义即视为多件", () => {
    const text = '```products\n[{"id":"a"},{"id":"b"}]\n```';
    expect(deriveSuggestions(text, FALLBACK)[0].label).toBe("对比这些商品");
  });

  it("把订单卡片映射为订单类追问", () => {
    const text = '```order\n{"id":"o1"}\n```';
    expect(deriveSuggestions(text, FALLBACK).map((s) => s.label)).toEqual([
      "跟踪该订单",
      "发起退货",
      "订单详情",
    ]);
  });

  it("一条消息含多种卡片时使用最后一张", () => {
    // 离输入框最近的那张卡片才是仍停留在屏幕上的。
    const text = `${productFence()}\n\`\`\`inventory\n{}\n\`\`\``;
    expect(deriveSuggestions(text, FALLBACK)[0].label).toBe("估算送达时间");
  });

  it("没有结构化负载时退回到收尾问句", () => {
    const out = deriveSuggestions("All set. Would you like to track it or return it?", FALLBACK);
    // 补足到三个——参见下面「始终恰好返回三个」的用例。
    expect(out.slice(0, 2).map((s) => s.label)).toEqual(["Track it", "Return it"]);
  });

  it("始终恰好返回三个，不足部分由兜底项补齐", () => {
    // 两个真实胶囊加一个固定胶囊，胜过两个胶囊加一个空位——这一行的高度
    // 不能在轮次之间变化。
    const out = deriveSuggestions("Would you like to track it or return it?", FALLBACK);
    expect(out).toHaveLength(3);
    expect(out[2]).toEqual(FALLBACK[0]);
  });

  it("绝不重复 label", () => {
    const out = deriveSuggestions("Would you like to check stock or check stock?", [
      { label: "查看库存", prompt: "dupe" },
      ...FALLBACK,
    ]);
    expect(new Set(out.map((s) => s.label)).size).toBe(out.length);
  });

  it("兜底项不足时也按其数量返回", () => {
    // 降级到现有内容，而不是抛错。
    expect(deriveSuggestions(undefined, FALLBACK.slice(0, 1))).toHaveLength(1);
  });
});
