"use client";

import ReactMarkdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { motion } from "framer-motion";
import { ChatProductCard } from "./product-card";
import { ChatOrderCard } from "./order-card";
import { ChatCheckoutCard } from "./checkout-card";
import { ChatReturnCard } from "./return-card";
import { ComparisonCard } from "./comparison-card";
import { ChatSentimentCard } from "./sentiment-card";
import { ChatInventoryCard } from "./inventory-card";
import { ChatPricingCard } from "./pricing-card";
import { listItem } from "@/lib/motion";
import {
  CardKind,
  validateCard,
} from "@/lib/chat-schemas";

const CardMotion = ({ children }: { children: React.ReactNode }) => (
  <motion.div variants={listItem} initial="hidden" animate="visible">
    {children}
  </motion.div>
);

// rehype-sanitize 的默认模式会剔除 `<script>`、on* 属性、
// `javascript:` URL 等。我们放行 class 名，以便 `<code>` / `<pre>` /
// `<table>` 上的 prose 样式继续生效——这是相对默认值的唯一偏离。
const SANITIZE_SCHEMA = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code ?? []), "className"],
  },
};

function SanitisedMarkdown({ children }: { children: string }) {
  return (
    <ReactMarkdown rehypePlugins={[[rehypeSanitize, SANITIZE_SCHEMA]]}>
      {children}
    </ReactMarkdown>
  );
}

interface RichMessageProps {
  content: string;
  streaming?: boolean;
  onAction?: (message: string) => void;
}

export function RichMessage({ content, streaming, onAction }: RichMessageProps) {
  const processed = streaming ? stripUnclosedFence(content) : content;
  const segments = parseContent(processed);

  return (
    <div className="space-y-3">
      {segments.map((seg, i) => {
        if (seg.type === "product" && seg.data) {
          return (
            <CardMotion key={i}>
              <ChatProductCard data={seg.data as any} onAction={onAction} />
            </CardMotion>
          );
        }
        if (seg.type === "order" && seg.data) {
          return (
            <CardMotion key={i}>
              <ChatOrderCard data={seg.data as any} onAction={onAction} />
            </CardMotion>
          );
        }
        if (seg.type === "checkout" && seg.data) {
          return (
            <CardMotion key={i}>
              <ChatCheckoutCard data={seg.data as any} />
            </CardMotion>
          );
        }
        if (seg.type === "return" && seg.data) {
          return (
            <CardMotion key={i}>
              <ChatReturnCard data={seg.data as any} />
            </CardMotion>
          );
        }
        if (seg.type === "comparison" && seg.data) {
          return (
            <CardMotion key={i}>
              <ComparisonCard products={seg.data as any} onAction={onAction} />
            </CardMotion>
          );
        }
        if (seg.type === "sentiment" && seg.data) {
          return (
            <CardMotion key={i}>
              <ChatSentimentCard data={seg.data as any} />
            </CardMotion>
          );
        }
        if (seg.type === "inventory" && seg.data) {
          return (
            <CardMotion key={i}>
              <ChatInventoryCard data={seg.data as any} onAction={onAction} />
            </CardMotion>
          );
        }
        if (seg.type === "pricing" && seg.data) {
          return (
            <CardMotion key={i}>
              <ChatPricingCard data={seg.data as any} />
            </CardMotion>
          );
        }
        return (
          <div
            key={i}
            className="prose prose-sm prose-slate max-w-none prose-p:my-1 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1 prose-ul:my-1 prose-ol:my-1 prose-strong:text-foreground dark:prose-invert"
          >
            <SanitisedMarkdown>{seg.text}</SanitisedMarkdown>
          </div>
        );
      })}
    </div>
  );
}

// ─── 类型 ─────────────────────────────────────────────────────────────

export interface Segment {
  type: string;
  text: string;
  data?: Record<string, unknown>;
}

// ─── 流式辅助：丢弃末尾未闭合的结构化代码块 ──────────────────────────
//
// 分块到达时，形如 ```product\n{"id":"p1"... 的代码块可能只写了一半。
// 我们不希望原始 JSON 在气泡里闪现——因此把未闭合的开始标记之后的所有
// 内容都隐藏起来，直到流结束。

function stripUnclosedFence(content: string): string {
  // `products` 排在 `product` 之前，让更长的类型优先匹配；不要求结尾
  // 有 \n——SSE 传输可能丢掉代码块标记后的换行，使
  // ```product\n{...} 塌缩为 ```product{...}。
  const openRegex = /```(products|product|order|checkout|return|sentiment|inventory|pricing)/g;
  let cursor = 0;
  let lastUnclosedStart = -1;
  let match;
  while ((match = openRegex.exec(content)) !== null) {
    const openStart = match.index;
    const afterOpen = openStart + match[0].length;
    const closeIdx = content.indexOf("```", afterOpen);
    if (closeIdx === -1) {
      lastUnclosedStart = openStart;
      break;
    }
    cursor = closeIdx + 3;
    openRegex.lastIndex = cursor;
  }
  if (lastUnclosedStart === -1) return content;
  return content.slice(0, lastUnclosedStart).trimEnd();
}

// ─── 1. 围栏代码块解析器（主路径） ───────────────────────────────────

function parseCodeBlocks(content: string): Segment[] | null {
  // 代码块标记后的换行是可选的：流式传输会把单独的 "\n" LLM token 封装
  // 成空的 SSE 数据行，于是它被丢弃，```product\n{...} 到达时变成
  // ```product{...}。我们容忍任意空白（包括没有空白），并要求正文以
  // { 或 [ 开头，这样偶然出现的 ```product-ideas 块就不会被误读成卡片。
  const codeBlockRegex = /```(products|product|order|checkout|return|sentiment|inventory|pricing)\s*([[{][\s\S]*?)```/g;
  const segments: Segment[] = [];
  let lastIndex = 0;
  let match;
  let found = false;

  while ((match = codeBlockRegex.exec(content)) !== null) {
    found = true;
    if (match.index > lastIndex) {
      const text = content.slice(lastIndex, match.index).trim();
      if (text) segments.push({ type: "text", text });
    }
    try {
      const data = JSON.parse(match[2].trim());
      if (match[1] === "products" && Array.isArray(data)) {
        // 两件商品 → 并排对比卡片
        if (data.length === 2) {
          const [p1, p2] = data.map((d: unknown) => validateCard("product", d));
          if (p1 && p2) {
            segments.push({ type: "comparison", text: "", data: [p1, p2] as any });
          } else {
            // 兜底：渲染为各自独立的卡片
            data.forEach((d: unknown) => {
              const validated = validateCard("product", d);
              if (validated) segments.push({ type: "product", text: "", data: validated });
            });
          }
        } else {
          data.forEach((d: unknown) => {
            const validated = validateCard("product", d);
            if (validated) {
              segments.push({ type: "product", text: "", data: validated });
            }
          });
        }
      } else {
        const kind = match[1] as CardKind;
        const validated = validateCard(kind, data);
        if (validated) {
          segments.push({ type: kind, text: "", data: validated });
        } else {
          // 模式校验拒绝了这份载荷——丢弃该代码块，而不是展示原始
          // JSON，与上面 "products" 数组分支既有的静默丢弃约定保持
          // 一致（那里校验失败的单件商品同样被丢弃，而非原样展示）。
          // 周围的对话文本不受影响——它已由本次匹配前后的 lastIndex
          // 记账捕获为自己的片段。此处记录日志，以便在不让用户看到的
          // 前提下仍能排查坏的 LLM 响应。
          if (process.env.NODE_ENV !== "production") {
            console.warn(`[rich-message] dropped a \`${kind}\` fence that failed schema validation`, data);
          }
        }
      }
    } catch (err) {
      // 已识别的代码块标记内出现了格式错误的 JSON——同样处理：
      // 丢弃它，不要把原始（损坏的）JSON 展示给用户。
      if (process.env.NODE_ENV !== "production") {
        console.warn(`[rich-message] dropped a \`${match[1]}\` fence with malformed JSON`, err);
      }
    }
    lastIndex = match.index + match[0].length;
  }

  if (!found) return null;

  if (lastIndex < content.length) {
    const text = content.slice(lastIndex).trim();
    if (text) segments.push({ type: "text", text });
  }

  return dedupeCards(suppressRedundantText(segments));
}

// 合并重复卡片。编排器经常会复述专业智能体返回的商品/订单数据，因此同一
// 条目可能到达两次——一次是干净的围栏块（→ 卡片），一次是塌缩围栏的副本
// （现在同样会变成卡片）。以「类型 + id」为键，让重复项只渲染一次。
function dedupeCards(segments: Segment[]): Segment[] {
  const seen = new Set<string>();
  return segments.filter((seg) => {
    let key: string | null = null;
    if (seg.type === "product" && seg.data) {
      const id = (seg.data as { id?: string }).id;
      key = id ? `product:${id}` : null;
    } else if (seg.type === "order" && seg.data) {
      const d = seg.data as { id?: string; order_id?: string };
      const id = d.id ?? d.order_id;
      key = id ? `order:${id}` : null;
    } else if (seg.type === "comparison" && Array.isArray(seg.data)) {
      key =
        "comparison:" +
        (seg.data as Array<{ id?: string }>).map((p) => p?.id ?? "").join(",");
    } else if (seg.type === "checkout" && seg.data) {
      // 结算卡没有 id——以购物车的形态为键（编排器经常在同一条消息里
      // 把同一份结算信息复述两次）。
      const d = seg.data as { item_count?: number; total?: number };
      key = `checkout:${d.item_count ?? ""}:${d.total ?? ""}`;
    } else if (seg.type === "return" && seg.data) {
      const d = seg.data as { return_id?: string; order_id?: string };
      key = `return:${d.return_id ?? d.order_id ?? ""}`;
    } else if (seg.type === "sentiment" && seg.data) {
      const d = seg.data as { product_id?: string; product_name?: string };
      key = `sentiment:${d.product_id ?? d.product_name ?? ""}`;
    } else if (seg.type === "inventory" && seg.data) {
      const d = seg.data as { product_id?: string; product_name?: string };
      key = `inventory:${d.product_id ?? d.product_name ?? ""}`;
    } else if (seg.type === "pricing" && seg.data) {
      const d = seg.data as { original_total?: number; final_total?: number; coupons?: unknown[] };
      key = `pricing:${d.original_total ?? ""}:${d.final_total ?? ""}:${d.coupons?.length ?? ""}`;
    }
    if (key === null) return true;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

// 移除那些只是相邻结构化卡片纯文本转储的文本片段。
function suppressRedundantText(segments: Segment[]): Segment[] {
  const cardTypes = new Set(["order", "product", "products", "checkout", "return"]);
  return segments.filter((seg, i) => {
    if (seg.type !== "text") return true;
    const prev = segments[i - 1];
    const next = segments[i + 1];
    const adjacentToCard =
      (prev && cardTypes.has(prev.type)) ||
      (next && cardTypes.has(next.type));
    return !(adjacentToCard && looksLikeStructuredDump(seg.text));
  });
}

function looksLikeStructuredDump(text: string): boolean {
  // 容忍行前缀：行可能以 "- "、"* "、项目符号开头，也可能什么都不带。
  // LINE = 行首、可选的项目符号/短横线、可选的空白。
  const LINE = "^[\\s]*[-*•]?[\\s]*";
  const patterns = [
    new RegExp(`${LINE}Status[:\\s]`, "im"),
    new RegExp(`${LINE}Order\\s*(Total|ID|#|Date|Placed)[:\\s]`, "im"),
    new RegExp(`${LINE}Tracking(\\s*Number)?[:\\s]`, "im"),
    new RegExp(`${LINE}(Shipping\\s*(Carrier|Address)|Carrier|Address)[:\\s]`, "im"),
    new RegExp(`${LINE}(Total|Subtotal|Amount)[:\\s]*\\$`, "im"),
    new RegExp(`${LINE}(Date|Placed)[:\\s]*\\d{4}-\\d{2}-\\d{2}`, "im"),
    /Items?\s*(in (?:this|your) order|:)/i,
    /[—–-]\s*\d+\s*[×x]\s*\$/m,
    /\(\s*\d+\s*[×x]\s*\$[\d,.]+\s*\)/, // "(1 × $189.99)"
    /Order\s*(Status\s*)?Timeline/i,
    new RegExp(`${LINE}Price[:\\s]*\\$`, "im"),
    new RegExp(`${LINE}Rating[:\\s]`, "im"),
  ];
  return patterns.filter((p) => p.test(text)).length >= 3;
}

// ─── 2. 订单文本识别（兜底） ─────────────────────────────────────────

interface OrderItem {
  name: string;
  quantity: number;
  unit_price: number;
  total?: number;
  category?: string;
  brand?: string;
}

interface TimelineEvent {
  status: string;
  date: string;
}

function parseOrderInText(content: string): Segment[] | null {
  // 必须在「订单」语境中出现一个 UUID
  if (
    !/Order\s*(?:ID|#)?[:\s]*[0-9a-f]{8}-/i.test(content)
  )
    return null;

  const paragraphs = content.split(/\n\n+/);

  const isOrderParagraph = (para: string): boolean => {
    const t = para.trim();
    return (
      /^Order\s*(Summary|ID|#)/im.test(t) ||
      /^Status[:\s]/im.test(t) ||
      /^Total[:\s]*\$/im.test(t) ||
      /(?:Shipping|Tracking|Carrier|Address|Placed)[:\s]/im.test(t) ||
      /^Items?\s*(in|:|\()/im.test(t) ||
      /[—–]\s*\d+\s*[×x]\s*\$/m.test(t) ||
      /^Order\s*Status\s*Timeline/im.test(t) ||
      /^\d{4}-\d{2}-\d{2}[:\s]/m.test(t)
    );
  };

  // 找出与订单相关的第一个和最后一个段落
  let startIdx = -1;
  let endIdx = -1;
  for (let i = 0; i < paragraphs.length; i++) {
    if (isOrderParagraph(paragraphs[i])) {
      if (startIdx === -1) startIdx = i;
      endIdx = i;
    } else if (startIdx !== -1) {
      // 在订单块之后的第一个非订单段落处停止
      break;
    }
  }
  if (startIdx === -1) return null;

  // 合并所有订单段落并提取结构化数据
  const orderText = paragraphs.slice(startIdx, endIdx + 1).join("\n\n");
  const orderData = extractOrderData(orderText);
  if (!orderData) return null;

  const segments: Segment[] = [];
  const introText = paragraphs.slice(0, startIdx).join("\n\n").trim();
  if (introText) segments.push({ type: "text", text: introText });
  segments.push({ type: "order", text: "", data: orderData as any });
  const outroText = paragraphs.slice(endIdx + 1).join("\n\n").trim();
  if (outroText) segments.push({ type: "text", text: outroText });
  return segments;
}

function extractOrderData(
  text: string
): Record<string, unknown> | null {
  const idMatch = text.match(
    /Order\s*(?:ID|#)?[:\s]*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i
  );
  if (!idMatch) return null;

  const id = idMatch[1];

  // 使用按行锚定的正则，避免匹配到零散词语
  const statusMatch = text.match(/^Status[:\s]+(\w[\w\s]*?)$/im);
  const status = statusMatch
    ? statusMatch[1].trim().toLowerCase().replace(/\s+/g, "_")
    : undefined;

  const totalMatch = text.match(/Total[:\s]*\$?([\d,]+\.?\d*)/i);
  const total = totalMatch
    ? parseFloat(totalMatch[1].replace(",", ""))
    : undefined;

  const trackingMatch = text.match(
    /Tracking\s*(?:Number)?[:\s]*(TRK\w+|\w{10,})/i
  );
  const tracking = trackingMatch ? trackingMatch[1] : undefined;

  const carrierMatch = text.match(
    /(?:Shipping\s*)?Carrier[:\s]*(.+?)(?:\n|$)/i
  );
  const carrier = carrierMatch ? carrierMatch[1].trim() : undefined;

  const addressMatch = text.match(
    /(?:Shipping\s*)?Address[:\s]*(.+?)(?:\n|$)/i
  );
  const shipping_address = addressMatch
    ? addressMatch[1].trim()
    : undefined;

  const dateMatch = text.match(
    /(?:Order\s*)?(?:Placed|Date)[:\s]*(.+?)(?:\n|$)/i
  );
  const date = dateMatch ? dateMatch[1].trim() : undefined;

  // 解析商品明细："Product Name (Brand, Category) — Qty × $Price = $Total"
  const items: OrderItem[] = [];
  const itemRegex =
    /^[-•*]?\s*(.+?)\s*\(([^)]+)\)\s*[—–-]\s*(\d+)\s*[×x]\s*\$([\d,.]+)(?:\s*=\s*\$([\d,.]+))?/gm;
  let m;
  while ((m = itemRegex.exec(text)) !== null) {
    const parts = m[2].split(/,\s*/);
    const unitPrice = parseFloat(m[4].replace(",", ""));
    const qty = parseInt(m[3]);
    items.push({
      name: m[1].trim(),
      quantity: qty,
      unit_price: unitPrice,
      total: m[5] ? parseFloat(m[5].replace(",", "")) : unitPrice * qty,
      brand: parts[0]?.trim() || undefined,
      category: parts[1]?.trim() || undefined,
    });
  }

  // 解析时间线："2026-03-25: Order placed"
  const timeline: TimelineEvent[] = [];
  const tlRegex = /^(\d{4}-\d{2}-\d{2})[:\s]+(.+?)$/gm;
  while ((m = tlRegex.exec(text)) !== null) {
    timeline.push({ status: m[2].trim(), date: m[1] });
  }

  return {
    id,
    status,
    total,
    tracking,
    carrier,
    shipping_address,
    date,
    items: items.length > 0 ? items : undefined,
    item_count: items.length || undefined,
    timeline: timeline.length > 0 ? timeline : undefined,
  };
}

// ─── 3. 商品文本识别（兜底） ─────────────────────────────────────────

function parseProductsInText(content: string): Segment[] | null {
  const paragraphs = content.split(/\n\n+/);
  if (paragraphs.length < 2) return null;

  const segments: Segment[] = [];
  let productCount = 0;

  for (const para of paragraphs) {
    const product = tryParseProductParagraph(para.trim());
    if (product) {
      productCount++;
      segments.push({ type: "product", text: "", data: product as any });
    } else {
      const text = para.trim();
      if (text) segments.push({ type: "text", text });
    }
  }

  if (productCount === 0) return null;
  return segments;
}

function tryParseProductParagraph(
  para: string
): Record<string, unknown> | null {
  const lines = para
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length < 2) return null;

  // 第一行 = 商品名称
  let nameLine = lines[0]
    .replace(/^\*\*/, "")
    .replace(/\*\*$/, "")
    .replace(/^[-•*]\s*/, "")
    .replace(/^\d+\.\s*/, "")
    .trim();

  if (nameLine.length < 3 || nameLine.length > 100) return null;
  // 跳过对话性/句子式的行
  if (
    /^(Hi |Hello|Would|Here |I |Let me|Based on|If you|These |This |For |Check|You |We |Our |Sure|Of course|Great|Thank)/i.test(
      nameLine
    )
  )
    return null;
  if (nameLine.endsWith("?") || nameLine.endsWith("!")) return null;

  // 块内某处必须有 "Price:" 行
  const hasPrice = lines.some((l) => /Price[:\s]*\$/i.test(l));
  const hasDollar = lines.some((l) => /\$\d/.test(l));
  const hasRating = lines.some((l) => /Rating[:\s]/i.test(l));

  if (!hasPrice && !(hasDollar && hasRating)) return null;

  let price: number | undefined;
  let original_price: number | undefined;
  let rating: number | undefined;
  let review_count: number | undefined;
  let description: string | undefined;
  let brand: string | undefined;
  let category: string | undefined;

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];

    // 价格行示例：Price: $299.99 (was $349.99)
    const priceMatch = line.match(/Price[:\s]*\$([\d,.]+)/i);
    if (priceMatch) {
      price = parseFloat(priceMatch[1].replace(",", ""));
      const wasMatch = line.match(/was\s*\$([\d,.]+)/i);
      if (wasMatch)
        original_price = parseFloat(wasMatch[1].replace(",", ""));
    }

    // 评分行示例：Rating: 4.7/5 (15 reviews)
    const ratingMatch = line.match(/Rating[:\s]*([\d.]+)\s*\/\s*5/i);
    if (ratingMatch) {
      rating = parseFloat(ratingMatch[1]);
      const revMatch = line.match(/\((\d+)\s*reviews?\)/i);
      if (revMatch) review_count = parseInt(revMatch[1]);
    }

    // 特性行示例：Features: ...
    if (/^Features?[:\s]/i.test(line)) {
      description = line.replace(/^Features?[:\s]*/i, "").trim();
    }

    // 显式的 Category / Brand 行
    if (/^Category[:\s]/i.test(line))
      category = line.replace(/^Category[:\s]*/i, "").trim();
    if (/^Brand[:\s]/i.test(line))
      brand = line.replace(/^Brand[:\s]*/i, "").trim();

    // "Why it's great:" —— 追加到描述
    if (/^Why\s/i.test(line)) {
      const extra = line.replace(/^Why\s+\S+\s*[:.]?\s*/i, "").trim();
      description = description ? `${description}. ${extra}` : extra;
    }
  }

  if (price === undefined) return null;

  // 从名称中提取品牌："AirPods Max (Apple)"
  const brandInName = nameLine.match(/\((\w[\w\s]*)\)$/);
  if (brandInName && !brand) {
    brand = brandInName[1].trim();
    nameLine = nameLine.replace(/\s*\(\w[\w\s]*\)$/, "").trim();
  }

  return {
    name: nameLine,
    price,
    original_price,
    rating,
    review_count,
    category,
    brand,
    description,
    on_sale: original_price != null && original_price > price,
  };
}

// ─── 守卫：剥离本解析器不认识、但形似 JSON 的代码块 ──────────────────
//
// parseCodeBlocks 只识别 5 种已知的卡片标记。目前所有后端实际发出的代码块
// 标记都落在其中（已对照每一个会输出代码块的提示词 YAML 验证过），因此这
// 是防御本解析器从未被教过的标记——LLM 幻觉出一个错误的标记名，或后端在
// 本文件更新之前先新增了一种卡片类型——而不是当前可达的路径。若没有这道
// 守卫，未识别的标记会原封不动落到 ReactMarkdown，后者把任何 ``` 代码块
// 都当作普通代码块处理并原样渲染其中的 JSON：正是本组件为 5 种已知标记
// 所防范的「绝不展示原始 JSON」的失败场景。
const KNOWN_CARD_TAGS = new Set(["products", "product", "order", "checkout", "return", "sentiment", "inventory", "pricing"]);
const ANY_FENCE_RE = /```([a-zA-Z0-9_-]*)\s*([[{][\s\S]*?)```/g;

function stripUnrecognizedJsonFences(content: string): string {
  return content.replace(ANY_FENCE_RE, (full, tag: string) => {
    if (KNOWN_CARD_TAGS.has(tag)) return full; // 这些交给 parseCodeBlocks 处理
    if (process.env.NODE_ENV !== "production") {
      console.warn(`[rich-message] dropped an unrecognized \`${tag || "(untagged)"}\` JSON-shaped fence`);
    }
    return "";
  });
}

// ─── 主解析器 ────────────────────────────────────────────────────────

export function parseContent(rawContent: string): Segment[] {
  const content = stripUnrecognizedJsonFences(rawContent);

  // 1. 围栏代码块（优先级最高，也最可靠）
  const codeBlockResult = parseCodeBlocks(content);
  if (codeBlockResult) return codeBlockResult;

  // 2. 纯文本中带商品明细的订单块
  const orderResult = parseOrderInText(content);
  if (orderResult) return orderResult;

  // 3. 纯文本中的商品块
  const productResult = parseProductsInText(content);
  if (productResult) return productResult;

  // 4. 默认：仅按 Markdown 处理
  return [{ type: "text", text: content }];
}
