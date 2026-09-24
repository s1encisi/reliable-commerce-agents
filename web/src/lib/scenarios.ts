/**
 * 演示场景注册表——把 README 中的 8 个场景以一键启动的形式呈现在落地页、
 * 首页仪表盘与 Cmd-K 命令面板上。
 *
 * 每个场景都会深链进对话并预填提示词，让面试官能在一分钟内跑完整个
 * 多智能体流程。
 */

import {
  Search,
  GitCompare,
  Package,
  RotateCcw,
  TrendingDown,
  Star,
  Warehouse,
  Shuffle,
  type LucideIcon,
} from "lucide-react";

export interface Scenario {
  id: string;
  label: string;
  /** 该场景演示内容的一句话说明。 */
  description: string;
  /** 发送给对话的完整提示词。 */
  prompt: string;
  /** 该场景会用到哪些专业智能体（用于展示的标签）。 */
  agents: string[];
  icon: LucideIcon;
  /**
   * 值得为该场景对比的编排模式（OrchestrationMode.name）——传给
   * mode-comparison.tsx。仅设置在两种模式对同一请求处理方式存在有意义
   * 差异的场景上（工具模式下逐轮的串行调用 vs. 工作流的分发，或中间件
   * 层与工作流内的人工审批关卡）；大多数场景没有值得对比之处，因此留空。
   */
  compareModes?: string[];
}

export const DEMO_SCENARIOS: Scenario[] = [
  {
    id: "product-search",
    label: "商品搜索",
    description: "在商品目录中做语义检索与价格筛选",
    prompt: "帮我找 3000 元以内、降噪效果好的无线耳机",
    agents: ["商品发现"],
    icon: Search,
    compareModes: ["tool", "workflow:pre-purchase"],
  },
  {
    id: "comparison",
    label: "并排对比",
    description: "从参数、价格与评论三方面对比两件商品",
    prompt: "对比一下 Sony WH-1000XM5 和 AirPods Max",
    agents: ["商品发现", "评论与情感分析"],
    icon: GitCompare,
  },
  {
    id: "order-tracking",
    label: "订单跟踪",
    description: "实时订单状态与物流所在地",
    prompt: "我最近一笔订单到哪了？",
    agents: ["订单管理"],
    icon: Package,
  },
  {
    id: "return-flow",
    label: "退货流程",
    description: "完整的退货资格判断与退货发起",
    prompt: "我想退掉上一笔订单",
    agents: ["订单管理"],
    icon: RotateCcw,
    compareModes: ["tool", "workflow:return-replace"],
  },
  {
    id: "price-check",
    label: "价格洞察",
    description: "30 天价格趋势与优惠力度信号",
    prompt: "Logitech MX Master 3S 现在算好价吗？",
    agents: ["定价与促销", "商品发现"],
    icon: TrendingDown,
  },
  {
    id: "review-analysis",
    label: "评论分析",
    description: "情感拆解、主题洞察与虚假评论识别",
    prompt: "大家对 Dyson V15 Detect 的评价怎么样？",
    agents: ["评论与情感分析"],
    icon: Star,
  },
  {
    id: "stock-check",
    label: "库存与履约",
    description: "各区域仓库的实时库存情况",
    prompt: "Dyson V15 Detect 有货吗？",
    agents: ["库存与履约"],
    icon: Warehouse,
  },
  {
    id: "multi-intent",
    label: "多意图请求",
    description: "单条提示词——编排器分发到多个专业智能体",
    prompt: "帮我把那件外套退掉，再找一件 2000 元以内更保暖的",
    agents: ["订单管理", "商品发现"],
    icon: Shuffle,
  },
];

/**
 * 前四个场景，以纯 label/prompt 对的形式导出——供首页仪表盘的快捷提示词
 * 胶囊行使用（保持向后兼容的结构）。
 */
export const QUICK_PROMPTS = DEMO_SCENARIOS.slice(0, 4).map(({ label, prompt }) => ({
  label,
  prompt,
}));

/** 构建一个可预填输入框的对话深链。 */
export function chatPromptHref(prompt: string): string {
  return `/chat?prompt=${encodeURIComponent(prompt)}`;
}

/** 构建一个公开助手深链（无需登录）。 */
export function shopAssistantHref(prompt: string): string {
  return `/shop/assistant?prompt=${encodeURIComponent(prompt)}`;
}
