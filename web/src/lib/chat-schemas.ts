import { z } from "zod";

/**
 * 对话渲染器用来校验「从 LLM 代码块中提取出的 JSON」的模式（schema）。
 * 任何未通过 `safeParse` 的内容都会回退到原始 Markdown 路径——绝不会渲染
 * 成一张残缺的卡片。
 *
 * 请保持这些模式*刻意宽松*：目标是「数据的形状像卡片知道如何渲染的对象」，
 * 而不是严格的服务端校验。智能体后端已经对形状做过规范化；这里的 Zod 是
 * 一道纵深防御关卡，用于防范：
 *   - 提示词注入产生的、带有控制字符或 HTML 的 JSON
 *   - 上游工具返回了错误的形状
 *   - LLM 幻觉出多余或缺失的字段
 */

// 基础类型：非空字符串，且不含可能被 ReactMarkdown 再次解析的内嵌标记。
const safeString = z
  .string()
  .max(2000)
  .transform((s) => s.normalize("NFKC"));

const optionalSafeString = safeString.optional();

const positiveNumber = z.number().finite().min(0).max(1_000_000);

export const ProductDataSchema = z
  .object({
    id: optionalSafeString,
    name: optionalSafeString,
    price: positiveNumber.optional(),
    original_price: positiveNumber.optional(),
    image_url: optionalSafeString,
    rating: z.number().min(0).max(5).optional(),
    review_count: z.number().int().min(0).optional(),
    category: optionalSafeString,
    brand: optionalSafeString,
    description: z.string().max(4000).optional(),
    on_sale: z.boolean().optional(),
  })
  .passthrough();

export const OrderItemSchema = z
  .object({
    name: optionalSafeString,
    quantity: z.number().int().positive().optional(),
    price: positiveNumber.optional(),
    unit_price: positiveNumber.optional(),
    subtotal: positiveNumber.optional(),
    brand: optionalSafeString,
    category: optionalSafeString,
    image_url: optionalSafeString,
  })
  .passthrough();

export const TimelineEventSchema = z
  .object({
    label: optionalSafeString,
    // order-card.tsx 渲染的是 event.status；后端可能发出其中任一字段
    status: optionalSafeString,
    date: optionalSafeString,
    completed: z.boolean().optional(),
  })
  .passthrough();

const ShippingAddressObj = z
  .object({
    street: optionalSafeString,
    city: optionalSafeString,
    state: optionalSafeString,
    zip: optionalSafeString,
    country: optionalSafeString,
  })
  .passthrough();

export const OrderDataSchema = z
  .object({
    id: optionalSafeString,
    order_id: optionalSafeString,
    status: optionalSafeString,
    total: positiveNumber.optional(),
    date: optionalSafeString,
    item_count: z.number().int().min(0).optional(),
    items: z.array(OrderItemSchema).max(50).optional(),
    tracking: optionalSafeString,
    carrier: optionalSafeString,
    shipping_address: z.union([safeString, ShippingAddressObj]).optional(),
    timeline: z.array(TimelineEventSchema).max(20).optional(),
    // 当智能体已经调用过 check_return_eligibility 时会被填充；
    // 缺失时 order-card.tsx 会回退到客户端启发式判断。
    return_eligible: z.boolean().optional(),
  })
  .passthrough();

export const CheckoutDataSchema = z
  .object({
    message: optionalSafeString,
    item_count: z.number().int().min(0).optional(),
    total: positiveNumber.optional(),
    subtotal: positiveNumber.optional(),
    discount: positiveNumber.optional(),
    items: z
      .array(
        z
          .object({
            name: optionalSafeString,
            brand: optionalSafeString,
            quantity: z.number().int().positive().optional(),
            price: positiveNumber.optional(),
            unit_price: positiveNumber.optional(),
            subtotal: positiveNumber.optional(),
          })
          .passthrough()
      )
      .max(50)
      .optional(),
    shipping_address: z.union([safeString, ShippingAddressObj]).optional(),
    address_ready: z.boolean().optional(),
  })
  .passthrough();

export const ReturnDataSchema = z
  .object({
    order_id: optionalSafeString,
    return_id: optionalSafeString,
    status: optionalSafeString,
    return_label_url: optionalSafeString,
    refund_amount: positiveNumber.optional(),
    refund_method: optionalSafeString,
    refund_timeline: optionalSafeString,
  })
  .passthrough();

// ── 生成式 UI 基础类型（第 8.4 阶段 Step 3） ─────────────────────────────
// 供可复用的 DataTable/TrendChart/DistributionChart/StatTile 组件
// （web/src/components/ui/）使用的通用形状。目前尚未在下文注册为它们
// 各自的代码块标记——第 4 阶段会在了解各专业智能体的真实数据形状后，
// 把它特定的代码块（例如情感分布或库存表标记）接到其中之一。现在就导出，
// 是为了让模式与其所校验的组件一同落地，延续「渲染层与其护栏作为一个
// 整体构建」的既有做法。

const SEMANTIC_TONE = z.enum(["success", "warning", "info", "destructive", "neutral"]);

export const DataTableColumnSchema = z
  .object({
    key: safeString,
    header: safeString,
    align: z.enum(["left", "center", "right"]).optional(),
  })
  .passthrough();

export const DataTableDataSchema = z
  .object({
    title: optionalSafeString,
    columns: z.array(DataTableColumnSchema).max(10),
    rows: z
      .array(z.record(z.string(), z.union([safeString, positiveNumber, z.boolean(), z.null()])))
      .max(50),
  })
  .passthrough();

export const TrendChartDataSchema = z
  .object({
    title: optionalSafeString,
    xKey: safeString,
    series: z.array(z.object({ key: safeString, label: safeString }).passthrough()).max(5),
    data: z.array(z.record(z.string(), z.union([safeString, z.number()]))).max(100),
  })
  .passthrough();

export const DistributionChartDataSchema = z
  .object({
    title: optionalSafeString,
    valueLabel: optionalSafeString,
    data: z.array(z.object({ label: safeString, value: z.number() }).passthrough()).max(50),
  })
  .passthrough();

export const StatTileDataSchema = z
  .object({
    label: safeString,
    value: z.union([safeString, positiveNumber]),
    hint: optionalSafeString,
    tone: SEMANTIC_TONE.optional(),
  })
  .passthrough();

// ── 评论与情感分析（第 8.4 阶段 Step 4a） ─────────────────────────────────
// 所有字段都可选：analyze_sentiment、get_sentiment_trend 与
// detect_fake_reviews 会在同一个 `sentiment` 代码块中各自填充不同的子集，
// 因此卡片只渲染实际存在的部分，而不要求三个工具调用全部完成才展示内容。

const MonthlyRatingPointSchema = z
  .object({
    month: safeString,
    average_rating: z.number().min(0).max(5),
    review_count: z.number().int().min(0).optional(),
  })
  .passthrough();

export const SentimentDataSchema = z
  .object({
    product_id: optionalSafeString,
    product_name: optionalSafeString,
    overall_sentiment: z.enum(["very_positive", "positive", "mixed", "negative", "very_negative"]).optional(),
    average_rating: z.number().min(0).max(5).optional(),
    total_reviews: z.number().int().min(0).optional(),
    rating_distribution: z.record(z.string(), z.number().int().min(0)).optional(),
    pros: z.array(safeString).max(10).optional(),
    cons: z.array(safeString).max(10).optional(),
    trend: z.enum(["improving", "declining", "stable", "insufficient_data"]).optional(),
    monthly_data: z.array(MonthlyRatingPointSchema).max(24).optional(),
    risk_level: z.enum(["high", "medium", "low"]).optional(),
    suspicious_count: z.number().int().min(0).optional(),
  })
  .passthrough();

// ── 库存与履约（第 8.4 阶段 Step 4b） ─────────────────────────────────────
// 所有字段都可选：check_stock、get_warehouse_availability 与
// get_restock_schedule 会在同一个 `inventory` 代码块中各自填充不同的子集，
// 与 `sentiment` 代码块「哪个工具跑过就展示哪部分」的设计一致。

const WarehouseStockSchema = z
  .object({
    warehouse: optionalSafeString,
    region: optionalSafeString,
    quantity: z.number().int().min(0).optional(),
    low_stock: z.boolean().optional(),
  })
  .passthrough();

const RestockEntrySchema = z
  .object({
    warehouse: optionalSafeString,
    region: optionalSafeString,
    expected_quantity: z.number().int().min(0).optional(),
    expected_date: optionalSafeString,
  })
  .passthrough();

// 第 8.4 阶段 Step 5——estimate_shipping 的字段。这是该代码块中唯一真正
// 具备交互性的场景：每个选项都是真实的选择而非仅仅展示，并接到「重新提问」
// 的路径上（见 inventory-card.tsx），因为「选择承运商」没有直接写入的接口
// ——这与购物车添加商品不同，后者已经有这样的接口。
const ShipsFromSchema = z
  .object({
    warehouse: optionalSafeString,
    region: optionalSafeString,
    quantity_available: z.number().int().min(0).optional(),
  })
  .passthrough();

const ShippingOptionSchema = z
  .object({
    carrier: safeString,
    speed_tier: optionalSafeString,
    price: positiveNumber,
    delivery_window: optionalSafeString,
  })
  .passthrough();

export const InventoryDataSchema = z
  .object({
    product_id: optionalSafeString,
    product_name: optionalSafeString,
    in_stock: z.boolean().optional(),
    total_quantity: z.number().int().min(0).optional(),
    warehouses: z.array(WarehouseStockSchema).max(20).optional(),
    upcoming_restocks: z.array(RestockEntrySchema).max(20).optional(),
    next_restock: optionalSafeString,
    ships_from: ShipsFromSchema.optional(),
    shipping_options: z.array(ShippingOptionSchema).max(10).optional(),
  })
  .passthrough();

// ── 定价与促销（第 8.4 阶段 Step 4c） ─────────────────────────────────────
// 所有字段都可选：optimize_cart 填充优惠瀑布相关的字段
// （original_total/savings/total_savings/final_total），get_active_deals
// 填充 coupons/promotions——与 4a/4b 同样是「哪个工具跑过就展示哪部分」的
// 设计。

const SavingsLineSchema = z
  .object({
    type: z.enum(["coupon", "bundle_promotion", "buy_x_get_y", "flash_sale", "loyalty_discount"]),
    code: optionalSafeString,
    name: optionalSafeString,
    description: optionalSafeString,
    tier: optionalSafeString,
    product: optionalSafeString,
    amount: positiveNumber,
  })
  .passthrough();

const DealCouponSchema = z
  .object({
    code: safeString,
    description: optionalSafeString,
    discount_type: z.enum(["percentage", "fixed"]).optional(),
    discount_value: positiveNumber.optional(),
    min_spend: positiveNumber.optional(),
    valid_until: optionalSafeString,
  })
  .passthrough();

const DealPromotionSchema = z
  .object({
    name: safeString,
    type: optionalSafeString,
    end_date: optionalSafeString,
  })
  .passthrough();

export const PricingDataSchema = z
  .object({
    original_total: positiveNumber.optional(),
    savings: z.array(SavingsLineSchema).max(10).optional(),
    total_savings: positiveNumber.optional(),
    final_total: positiveNumber.optional(),
    savings_percentage: z.number().min(0).max(100).optional(),
    coupons: z.array(DealCouponSchema).max(20).optional(),
    promotions: z.array(DealPromotionSchema).max(20).optional(),
  })
  .passthrough();

export type CardKind = "product" | "order" | "checkout" | "return" | "sentiment" | "inventory" | "pricing";

const SCHEMAS = {
  product: ProductDataSchema,
  order: OrderDataSchema,
  checkout: CheckoutDataSchema,
  return: ReturnDataSchema,
  sentiment: SentimentDataSchema,
  inventory: InventoryDataSchema,
  pricing: PricingDataSchema,
} as const;

/**
 * 递归删除值为 `null` 的键（以及数组中的 `null` 元素）。
 * LLM/后端对空字段会发出 `null`（例如 "shipping_address": null），但 Zod
 * 的 `.optional()` 接受 `undefined` 而不接受 `null`——因此一个多余的 null
 * 会让整个模式校验失败，从而丢掉一张本应有效的卡片。在校验之前把 null
 * 视为「不存在」。
 */
function dropNulls(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.filter((v) => v !== null).map(dropNulls);
  }
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      if (v === null) continue;
      out[k] = dropNulls(v);
    }
    return out;
  }
  return value;
}

/**
 * 用 `kind` 对应的模式校验已解析的 JSON 值。成功时返回清洗后的数据，
 * 校验失败时返回 `null`——渲染器把 `null` 视为「丢弃该卡片，改为渲染
 * 原始文本」。
 */
export function validateCard<K extends CardKind>(
  kind: K,
  raw: unknown
): z.infer<(typeof SCHEMAS)[K]> | null {
  const result = SCHEMAS[kind].safeParse(dropNulls(raw));
  return result.success ? (result.data as z.infer<(typeof SCHEMAS)[K]>) : null;
}
