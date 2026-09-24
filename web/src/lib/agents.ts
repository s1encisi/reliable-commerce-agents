/**
 * 所有专业智能体与编排器的静态元数据。
 * 智能体索引页、详情页与智能体市场的唯一数据来源。
 */

import {
  Search,
  Package,
  Tag,
  Star,
  Warehouse,
  Network,
  type LucideIcon,
} from "lucide-react";

export interface AgentTool {
  name: string;
  description: string;
}

export interface AgentMeta {
  slug: string;
  /** 与后端 usage_logs.agent_name 对应。 */
  backendName: string;
  name: string;
  role: string;
  tagline: string;
  description: string;
  icon: LucideIcon;
  /** 强调色使用的 Tailwind 文本色类（浅色 + 深色）。 */
  accentText: string;
  /** 图标底色使用的 Tailwind 背景色类（浅色 + 深色）。 */
  accentBg: string;
  tools: AgentTool[];
  examplePrompts: string[];
}

export const AGENTS: AgentMeta[] = [
  {
    slug: "product-discovery",
    backendName: "product-discovery",
    name: "商品发现",
    role: "搜索与推荐",
    tagline: "基于语义相似度的自然语言商品搜索与个性化推荐。",
    description:
      "商品发现智能体能够理解您真正想要的东西——无论您的描述是精确还是模糊。它结合关键词过滤、pgvector 语义向量嵌入与价格趋势数据，为您筛选出合适的商品，并读取您的购买历史来做个性化排序。",
    icon: Search,
    accentText: "text-cyan-600 dark:text-cyan-400",
    accentBg: "bg-cyan-50 dark:bg-cyan-950",
    tools: [
      { name: "search_products", description: "在商品目录中按分类、价格区间、评分或任意组合进行关键词加条件筛选搜索。" },
      { name: "semantic_search", description: "基于向量嵌入的相似度检索（pgvector）。最适合「想要一件冬天宅家穿的暖和的衣服」这类模糊描述。" },
      { name: "get_product_details", description: "获取指定商品的完整参数、价格、库存状态与商家信息。" },
      { name: "compare_products", description: "对 2–3 件商品做属性并排对比。" },
      { name: "find_similar_products", description: "基于 1536 维嵌入的余弦相似度，找出与指定商品相似的商品。" },
      { name: "get_trending_products", description: "按近期订单量排序的热门商品，可按分类筛选。" },
      { name: "get_price_history", description: "30/60/90 天价格趋势，包含均价、最低价、最高价与优惠力度信号。" },
      { name: "check_stock", description: "查询各区域仓库的实时库存数量。" },
      { name: "get_user_profile", description: "当前用户的会员等级与账户信息，用于个性化推荐。" },
      { name: "get_purchase_history", description: "近期订单记录，用于根据历史偏好调整推荐结果。" },
    ],
    examplePrompts: [
      "帮我找 2000 元以内、评价好的无线降噪耳机",
      "想买点适合在家办公用的舒服东西",
      "对比一下 Sony WH-1000XM5 和 AirPods Max",
      "Logitech MX Master 3S 最近的价格走势怎么样？",
      "看看本周热门的数码产品",
    ],
  },
  {
    slug: "order-management",
    backendName: "order-management",
    name: "订单管理",
    role: "订单、退货与购物车",
    tagline: "覆盖订单全生命周期——物流跟踪、取消、退货、退款与购物车操作。",
    description:
      "订单管理智能体负责您下单之后的一切事务。它可以查询任意订单、实时跟踪物流、对尚未发货的订单执行取消与修改、处理完整的退货退款流程，并管理您的购物车——包括使用优惠券与设置收货地址。",
    icon: Package,
    accentText: "text-blue-600 dark:text-blue-400",
    accentBg: "bg-blue-50 dark:bg-blue-950",
    tools: [
      { name: "get_user_orders", description: "列出您的近期订单，可按状态筛选（已下单、已发货、已送达、已取消等）。" },
      { name: "get_order_details", description: "订单完整信息，包含商品明细、价格、状态流转记录与收货地址。" },
      { name: "get_order_tracking", description: "指定订单的实时物流状态与承运商所在地。" },
      { name: "cancel_order", description: "取消订单——仅对「已下单」或「已确认」状态的订单可用。" },
      { name: "modify_order", description: "修改尚未发货订单的收货地址。" },
      { name: "check_return_eligibility", description: "根据售后政策与购买日期，判断订单或商品是否符合退货条件。" },
      { name: "initiate_return", description: "发起退货流程并生成退货面单。" },
      { name: "process_refund", description: "在收到退货后执行退款。" },
      { name: "get_return_status", description: "跟踪进行中的退货或退款进度。" },
      { name: "add_to_cart", description: "按商品 ID 或商品名称将商品加入购物车。" },
      { name: "get_cart", description: "当前购物车内容，包含数量、小计与已享受的优惠。" },
      { name: "remove_from_cart", description: "从购物车中移除指定商品。" },
      { name: "update_cart_quantity", description: "修改购物车中已有商品的购买数量。" },
      { name: "apply_coupon_to_cart", description: "为购物车使用优惠券并重新计算总价。" },
      { name: "set_shipping_address", description: "设置或更新购物车的收货地址。" },
    ],
    examplePrompts: [
      "我最近一笔订单到哪了？",
      "帮我取消订单 #55e8400——尺码买错了",
      "上周买的那件外套可以退货吗？",
      "把 2 副 Sony WH-1000XM5 加入购物车",
      "我购物车里现在有什么？",
    ],
  },
  {
    slug: "pricing-promotions",
    backendName: "pricing-promotions",
    name: "定价与促销",
    role: "优惠、优惠券与会员权益",
    tagline: "优惠券校验、购物车优化、会员折扣与组合优惠发掘。",
    description:
      "定价与促销智能体帮您找到最划算的方案。它会校验优惠券是否可用、识别进行中的促销活动、检查组合优惠资格，并计算您的会员等级折扣。购物车优化器更进一步——它会评估所有可叠加的优惠，返回最省钱的那一种组合。",
    icon: Tag,
    accentText: "text-amber-600 dark:text-amber-400",
    accentBg: "bg-amber-50 dark:bg-amber-950",
    tools: [
      { name: "validate_coupon", description: "校验优惠券是否在有效期、是否满足最低消费、是否超出使用次数及分类限制。" },
      { name: "optimize_cart", description: "评估所有可用优惠券、促销活动与会员折扣，返回最优组合方案。" },
      { name: "get_active_deals", description: "列出当前所有进行中的促销活动与未过期的公开优惠券。" },
      { name: "check_bundle_eligibility", description: "判断一组商品是否符合组合优惠条件。" },
      { name: "get_loyalty_tier", description: "您当前的会员等级（青铜/白银/黄金）及其可享受的权益。" },
      { name: "calculate_loyalty_discount", description: "根据购物车总价计算会员折扣金额。" },
      { name: "get_loyalty_benefits", description: "对比各会员等级——消费门槛、折扣比例、包邮权益、专属客服。" },
      { name: "get_price_history", description: "历史价格数据，用于判断当前是否真的是好价。" },
      { name: "get_purchase_history", description: "历史订单记录，用于计算您的会员等级与可享权益。" },
    ],
    examplePrompts: [
      "我今天有什么可用的优惠券吗？",
      "我购物车里现在怎样买最划算？",
      "帮我看看优惠券 SAVE20 能不能用",
      "我是什么会员等级？有哪些权益？",
      "我现在的购物车能参加哪些组合优惠？",
    ],
  },
  {
    slug: "review-sentiment",
    backendName: "review-sentiment",
    name: "评论与情感分析",
    role: "评论与情感分析",
    tagline: "深度评论分析——情感拆解、主题洞察、趋势跟踪与虚假评论识别。",
    description:
      "评论与情感分析智能体不止看星级评分。它按主题拆解评论（质量、性价比、设计、物流），跟踪情感随时间的变化，识别可疑或虚假评论，并从用户反馈中提炼出真实信号。商家还可以用它来起草专业的评论回复。",
    icon: Star,
    accentText: "text-emerald-600 dark:text-emerald-400",
    accentBg: "bg-emerald-50 dark:bg-emerald-950",
    tools: [
      { name: "get_product_reviews", description: "分页获取商品评论，可按最新、最有帮助或评分排序。" },
      { name: "analyze_sentiment", description: "聚合情感分析：平均评分、评分分布，以及从评论文本提炼的优缺点总结。" },
      { name: "get_sentiment_by_topic", description: "将评论按主题（质量、性价比、物流、设计、耐用性）拆解，给出各主题的提及次数与平均评分。" },
      { name: "get_sentiment_trend", description: "按月统计的历史平均评分，用于判断口碑在变好还是变差。" },
      { name: "detect_fake_reviews", description: "通过核查是否真实购买、语言是否模板化、评分是否异常来标记可疑评论。" },
      { name: "search_reviews", description: "在指定商品的评论标题与正文中做关键词搜索。" },
      { name: "draft_seller_response", description: "为一条评论起草专业的商家回复（需要商家或管理员角色）。" },
      { name: "compare_product_reviews", description: "跨商品评论对比——适合在两件相似商品之间做决策。" },
    ],
    examplePrompts: [
      "大家对 Sony WH-1000XM5 的评价怎么样？",
      "商品 X 下面有虚假评论吗？",
      "给我看看这个商品近 6 个月的口碑趋势",
      "评论里怎么说做工质量？",
      "对比一下评论：Sony WH-1000XM5 vs AirPods Max",
    ],
  },
  {
    slug: "inventory-fulfillment",
    backendName: "inventory-fulfillment",
    name: "库存与履约",
    role: "库存、配送与物流",
    tagline: "实时库存查询、配送时效估算、承运商对比与履约方案规划。",
    description:
      "库存与履约智能体为您提供三大区域仓库的准确库存情况以及可选的配送方式。它会根据您所在地区估算送达时间与运费、对比承运商、跟踪物流，并能为一单多件的订单计算出最优履约方案。",
    icon: Warehouse,
    accentText: "text-slate-600 dark:text-slate-300",
    accentBg: "bg-slate-100 dark:bg-slate-800",
    tools: [
      { name: "check_stock", description: "查询某商品在东部、中部、西部三个仓库的实时库存。" },
      { name: "get_warehouse_availability", description: "各仓库库存情况以及即将到货的补货计划。" },
      { name: "get_restock_schedule", description: "所有仓库的近期补货日期与预计数量。" },
      { name: "estimate_shipping", description: "从最近的备货仓库到您所在区域的运费与送达时间。" },
      { name: "compare_carriers", description: "承运商并排对比（标准、加急、次日达），包含价格与预计送达时间。" },
      { name: "get_tracking_status", description: "订单的最新物流轨迹与承运商所在地。" },
      { name: "calculate_fulfillment_plan", description: "为一单多件的订单规划最优仓库分配方案，并估算总运费。" },
      { name: "place_backorder", description: "为缺货商品登记缺货预订——仅在确实无法供货时才创建预订。" },
    ],
    examplePrompts: [
      "Sony WH-1000XM5 有货吗？",
      "寄到东部地区要多久？",
      "帮我对比一下标准配送和加急配送",
      "Dyson V15 Detect 什么时候补货？",
      "3 件商品寄到西部，怎么寄最便宜？",
    ],
  },
  {
    slug: "orchestrator",
    backendName: "orchestrator",
    name: "编排器",
    role: "路由与协调",
    tagline: "统一入口——通过 A2A 协议把每个请求路由给合适的专业智能体。",
    description:
      "编排器是每次对话的入口。它会识别您的意图，选择合适的专业智能体（或智能体组合），并协调最终的回复。它通过 A2A 协议（Agent-to-Agent，智能体间通信协议）以 HTTP 调用各专业智能体，并转发您的身份与会话上下文，让专业智能体能够给出个性化、贴合上下文的回答。",
    icon: Network,
    accentText: "text-primary",
    accentBg: "bg-primary/10",
    tools: [
      {
        name: "call_specialist_agent",
        description:
          "通过 HTTP POST 调用专业智能体的 A2A 端点，把请求路由给对应的专业智能体（商品发现、订单管理、定价与促销、评论与情感分析、库存与履约），并在请求头中转发用户身份、角色与会话上下文。",
      },
    ],
    examplePrompts: [
      "帮我找 2000 元以内、评价好的无线降噪耳机，顺便看看有没有货",
      "我购物车里怎样买最划算？大概什么时候能送到？",
      "给我看看预算范围内评分最高那件商品的评论",
    ],
  },
];

/** 按 slug 查找智能体。未知 slug 返回 undefined。 */
export function getAgent(slug: string): AgentMeta | undefined {
  return AGENTS.find((a) => a.slug === slug);
}
