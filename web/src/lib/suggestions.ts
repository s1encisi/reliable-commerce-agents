import type { CardKind } from "./chat-schemas";

/**
 * 由助手最后一条消息推导出的追问胶囊。
 *
 * Issue #4 提出对助手结尾的提问做正则匹配。这可行，但客户端上已经有一个
 * 更强的信号：消息里带类型的生成式 UI 载荷。一个 ```product 代码块意味着
 * 用户正在看商品，无论周围是什么文案——它确定、可测试，且不受模型措辞或
 * 语言影响。
 *
 * 因此文本解析在这里只是*次要*层级，而非主要层级。
 *
 * 刻意不做 LLM 调用。用模型生成建议会给每一轮对话增加延迟与成本，并且需要
 * 一个 #4 的「仅前端」约束所不允许的接口。这里只是一个对客户端已持有文本
 * 的纯函数。
 */

export interface Suggestion {
  label: string;
  prompt: string;
}

/** 各卡片类型对应的胶囊。按可能性从高到低排序；只展示前三个。 */
const BY_CARD: Partial<Record<CardKind, Suggestion[]>> = {
  product: [
    { label: "查看库存", prompt: "这件商品有货吗？" },
    { label: "查看评论", prompt: "这件商品的评论怎么说？" },
    { label: "找相似商品", prompt: "给我看看相似的商品" },
  ],
  order: [
    { label: "跟踪该订单", prompt: "这笔订单现在到哪了？" },
    { label: "发起退货", prompt: "我想退掉这笔订单" },
    { label: "订单详情", prompt: "把这笔订单的完整详情给我看看" },
  ],
  pricing: [
    { label: "有更划算的吗？", prompt: "这件商品还有更划算的优惠吗？" },
    { label: "价格历史", prompt: "这件商品的价格历史是怎样的？" },
    { label: "使用优惠券", prompt: "我有什么可用的优惠券吗？" },
  ],
  inventory: [
    { label: "估算送达时间", prompt: "这件商品最快多久能送到我这里？" },
    { label: "其他仓库", prompt: "还有哪些仓库有这件商品？" },
    { label: "补货时间", prompt: "这件商品什么时候补货？" },
  ],
  sentiment: [
    { label: "查看差评", prompt: "差评都说了些什么？" },
    { label: "总结优点", prompt: "评论者最喜欢这件商品的哪一点？" },
    { label: "对比口碑", prompt: "和同类商品相比，它的口碑如何？" },
  ],
  return: [
    { label: "退货进度", prompt: "我的退货进度如何？" },
    { label: "退款时间", prompt: "我什么时候能收到退款？" },
    { label: "换货选项", prompt: "我可以换成什么？" },
  ],
  checkout: [
    { label: "使用优惠券", prompt: "帮我用上最优惠的券" },
    { label: "配送方式", prompt: "有哪些配送方式可选？" },
    { label: "提交订单", prompt: "直接下单吧" },
  ],
};

/** 多于一件商品时，有用的追问会从「介绍一下」变为「对比一下」。 */
const MULTI_PRODUCT: Suggestion[] = [
  { label: "对比这些商品", prompt: "帮我对比一下这几件商品" },
  { label: "查看库存", prompt: "这几件里哪些有货？" },
  { label: "有优惠吗？", prompt: "这几件有在促销的吗？" },
];

const FENCE = /```(product|products|order|checkout|return|sentiment|inventory|pricing)\s*\n/g;

/**
 * 一条消息中出现的卡片类型，按出现顺序排列，且保留重复项。
 *
 * 用计数而非去重，因为「两件商品」和「一件商品」适用的追问并不相同。
 */
export function cardKindsIn(text: string): string[] {
  const kinds: string[] = [];
  for (const match of text.matchAll(FENCE)) {
    kinds.push(match[1]);
  }
  return kinds;
}

const MAX_LABEL = 42;
const MIN_LABEL = 3;

/** 首字母大写、去掉标点、做长度校验。不可用时返回 null。 */
function cleanCandidate(raw: string): string | null {
  const text = raw
    .replace(/^[\s,，、;:–—-]+|[\s,，、;:。！？–—-]+$/g, "")
    .replace(/\s+/g, " ")
    .trim();

  if (text.length < MIN_LABEL || text.length > MAX_LABEL) return null;
  // 含有代码块标记或换行的候选属于解析残渣。
  if (/[\n`{}[\]]/.test(text)) return null;

  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * 从助手的结尾提问中生成胶囊。
 *
 * 只取最后一句，且只在这一句是疑问句时——任何更宽松的规则都会把回答中间
 * 的叙述变成胶囊，这正是该层级容易出洋相的地方。如果无法产出至少两个干净
 * 的候选，它就不返回任何内容，让调用方继续往下走，而不是在两个人造胶囊旁
 * 摆一个奇怪的胶囊。
 *
 * 中英文都要认：句末既可能是半角 `?`，也可能是全角 `？`；问句的引导语既
 * 可能是 `Would you like to` 这类英文前缀，也可能是「您想」「要不要我」这类
 * 中文前缀。界面已经是中文的，只认半角问号会让这一整层在中文下静默失效。
 */

/** 问句的引导语——无论中英文，都是修饰语而非候选本身，需要整段剥掉。 */
const CLOSING_PREFIX =
  /^(?:would you like(?: me)? to|do you want me to|shall i|should i|can i help you|要不要我|要不要|需要我|您想|是否|我可以|能否|可以帮|想不想)\s*/i;

/** 候选之间的分隔符：英文的 `,` 与 ` or `，以及中文的 `，`、`、`、「还是」「或者」。 */
const CLOSING_SEPARATOR =
  /\s*[,，、]\s*(?:or|还是|或者)?\s*|\s*(?:还是|或者)\s*|\s+or\s+/i;

export function fromClosingQuestion(text: string): Suggestion[] {
  const trimmed = text.trimEnd();
  if (!/[?？]$/.test(trimmed)) return [];

  // 中文句末标点后不跟空格，所以这里用零宽切分；但零宽切分在字符串末尾会
  // 多出一个空串，取最后一句前必须先滤掉。
  const lastSentence =
    trimmed
      .split(/(?<=[。！？])|(?<=[.!?])\s+/)
      .filter((s) => s.length > 0)
      .pop() ?? "";
  if (!/[?？]$/.test(lastSentence) || lastSentence.length > 240) return [];

  const body = lastSentence.replace(/[?？]+$/, "").replace(CLOSING_PREFIX, "").trim();

  const parts = body
    .split(CLOSING_SEPARATOR)
    .map(cleanCandidate)
    .filter((c): c is string => c !== null);

  const unique = [...new Set(parts)];
  if (unique.length < 2) return [];

  return unique.slice(0, 3).map((label) => ({ label, prompt: label }));
}

/**
 * 输入框用的追问胶囊。
 *
 * 三个层级，先命中非空者胜出，并始终用 `fallback` 补齐，因此胶囊行既不会
 * 变短，也不会渲染为空。
 *
 * @param lastAssistantMessage 助手最近一条消息的文本（如果有）。
 * @param fallback 当前的静态建议——即第三层级的行为，保持不变。
 */
export function deriveSuggestions(
  lastAssistantMessage: string | undefined,
  fallback: Suggestion[]
): Suggestion[] {
  const take3 = (list: Suggestion[]): Suggestion[] => {
    const seen = new Set<string>();
    const out: Suggestion[] = [];
    for (const s of [...list, ...fallback]) {
      if (out.length === 3) break;
      if (seen.has(s.label)) continue;
      seen.add(s.label);
      out.push(s);
    }
    return out;
  };

  if (!lastAssistantMessage?.trim()) return take3([]);

  // ── 第一层级：带类型的载荷 ──────────────────────────────────────────
  const kinds = cardKindsIn(lastAssistantMessage);
  if (kinds.length > 0) {
    // `products` 代码块本身是数组，因此天然属于多件；两个及以上的独立
    // `product` 代码块同理。
    const productCount = kinds.filter((k) => k === "product").length;
    const isMultiProduct = kinds.includes("products") || productCount > 1;

    if (isMultiProduct) return take3(MULTI_PRODUCT);

    // 最后一张卡片胜出：它离输入框最近，也是用户最可能仍在看的那张。
    const last = kinds[kinds.length - 1];
    const key = (last === "products" ? "product" : last) as CardKind;
    const mapped = BY_CARD[key];
    if (mapped) return take3(mapped);
  }

  // ── 第二层级：结尾提问 ─────────────────────────────────────────────
  const fromProse = fromClosingQuestion(lastAssistantMessage);
  if (fromProse.length > 0) return take3(fromProse);

  // ── 第三层级：保持不变 ──────────────────────────────────────────────
  return take3([]);
}
