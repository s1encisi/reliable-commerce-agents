export interface TaskRequirements {
  product_ids?: string[];
  max_price?: number | null;
  min_rating?: number | null;
  category?: string | null;
  min_items?: number;
  in_stock?: boolean | null;
  compare?: boolean;
}
export interface TaskInput {
  goal: string;
  constraints: string[];
  requirements: TaskRequirements;
}
export interface TaskStep {
  id: string;
  service: string;
  instruction: string;
  depends_on: string[];
  done_when?: string;
  acceptance: {
    kind: "product_selection" | "product_comparison" | "inventory_check" | "order_lookup";
    required_ids: string[];
    min_items: number;
    from_steps: string[];
  };
}
export interface TaskReceipt {
  receipt_id: string;
  verified_at: number;
  valid_until: number;
  result: { response?: string; [key: string]: unknown };
  verification?: {
    passed: boolean;
    errors: string[];
    evidence: Record<string, unknown>[];
    validation_scope: string[];
  };
}
export interface TaskRecord extends TaskInput {
  id: string;
  revision: number;
  status: "pending" | "planning" | "ready" | "running" | "needs_replan" | "needs_review" | "completed";
  planning_attempts: number;
  review_items?: string[];
  active_step: string | null;
  plan: { source?: string; goal: string; constraints: string[]; steps: TaskStep[] } | null;
  results: Record<string, TaskReceipt>;
  receipt_history: unknown[];
}
export const TASK_STATUS_LABELS: Record<TaskRecord["status"], string> = {
  pending: "待规划", planning: "规划中", ready: "等待执行下一步", running: "步骤执行中",
  needs_replan: "需要重新规划", needs_review: "任务要求待核实", completed: "任务已完成",
};
export const SERVICE_LABELS: Record<string, string> = {
  "product-discovery": "商品发现", "order-management": "订单查询", "pricing-promotions": "定价与促销",
  "review-sentiment": "评论分析", "inventory-fulfillment": "库存与履约",
};
