/** 展示文案与机器协议分离；不改写 outcome、error_code 或数据库状态。 */
const OUTCOMES: Record<string, string> = {
  READY: "符合申请条件", NEEDS_INPUT: "需要补充信息", NEEDS_REVIEW: "需要人工核实",
  AWAITING_APPROVAL: "等待管理员审批", SUCCEEDED: "退货申请已创建", REJECTED: "申请已拒绝",
  RETRYABLE_FAILURE: "可重试的失败", UNKNOWN: "结果待核实", FAILED_FINAL: "操作未完成",
};
const MESSAGES: Record<string, string> = {
  RETURN_EXISTS: "该订单已有退货记录，请查看订单详情。",
  ORDER_NOT_DELIVERED: "仅已签收订单可申请退货。",
  DELIVERY_TIME_MISSING: "缺少签收时间，需要人工核实；尚未创建退货。",
  DELIVERY_TIME_INVALID: "签收时间与订单记录不一致，需要人工核实；尚未创建退货。",
  DELIVERY_TIME_CONFLICT: "签收记录存在冲突，需要人工核实；尚未创建退货。",
  RETURN_WINDOW_EXPIRED: "已超过签收后 30 天的退货申请期限，未创建退货。",
  RETURN_ELIGIBLE: "订单符合退货申请条件。",
  AUTH_REQUIRED: "请登录后操作。", ORDER_NOT_FOUND: "订单不存在或您无权访问。",
  VALIDATION_ERROR: "申请参数不完整或格式不正确，请检查后重试。",
  OPERATION_CONFLICT: "此操作标识已绑定其他申请参数，请核实原申请。",
  POLICY_CHANGED: "退货政策已更新，请核实后发起新申请。",
  APPROVAL_STALE: "订单或申请信息已变更，需要重新审批。",
  APPROVAL_INVALID: "当前审批不能用于执行，请联系管理员核实。",
  APPROVAL_DENIED: "管理员已拒绝申请，未创建退货。",
  APPROVAL_EXPIRED: "审批已过期，请重新发起申请。",
  OPERATION_NOT_FOUND: "未找到此操作或您无权访问，请核实申请记录。",
  UNKNOWN_OUTCOME: "执行结果尚未确认，请查询同一操作的状态。",
  DEADLINE_EXCEEDED: "请求超时，结果尚未确认；请先查询状态再决定是否重试。",
  DEPENDENCY_UNAVAILABLE: "服务暂时不可用，请保留此操作标识并查询状态。",
  OPERATION_BUSY: "申请仍在处理中，请先查询状态。",
};
const OUTCOME_MESSAGES: Record<string, string> = {
  READY: "订单符合申请条件。",
  NEEDS_INPUT: "请补充申请信息后继续。",
  NEEDS_REVIEW: "申请需要人工核实，尚未创建退货。",
  AWAITING_APPROVAL: "退货申请已提交，等待管理员审批；尚未创建退货或发放退款。",
  SUCCEEDED: "退货申请已创建，尚未发放退款。",
  REJECTED: "申请已拒绝，未创建退货。",
  RETRYABLE_FAILURE: "本次未完成，请保留同一操作标识并先查询状态。",
  UNKNOWN: "执行结果尚未确认，请查询同一操作的状态。",
  FAILED_FINAL: "操作未完成，请核实原因后再发起新申请。",
};
const DETAILS: Record<string, string> = {
  "Invalid email or password": "邮箱或密码不正确。",
  "Email already registered": "该邮箱已注册，请直接登录。",
  "Account is deactivated": "账号已停用，请联系管理员。",
  "Admin access required": "此操作需要管理员权限。",
  "Seller access required": "此操作需要商家权限。",
  "Order not found": "订单不存在或您无权访问。",
  "Conversation not found": "对话不存在或您无权访问。",
  "Product not found": "未找到该商品。",
  "Request was already resolved by another admin": "该申请已由其他管理员处理，请刷新状态。",
  "No pending approval found for this run": "该运行没有待处理审批，请刷新状态。",
  "checkpoint not found": "未找到可恢复的检查点，请核实运行状态。",
};

export function outcomeLabel(value?: string): string {
  return value ? OUTCOMES[value] ?? "状态待核实" : "状态待核实";
}
export function returnStatusLabel(value?: string): string {
  const labels: Record<string, string> = { requested: "已创建申请", pending: "待处理", approved: "已批准", received: "已收到退回商品", completed: "已完成", rejected: "已拒绝", cancelled: "已取消" };
  return value ? labels[value] ?? OUTCOMES[value] ?? "状态待核实" : "状态待核实";
}
export function refundMethodLabel(value?: string): string {
  return value === "original_payment" ? "原路退回" : value === "store_credit" ? "退至平台余额" : "退款方式待核实";
}
export function businessMessage(data: { outcome?: string; error_code?: string; message?: string; status?: string }): string {
  if (data.error_code && MESSAGES[data.error_code]) return MESSAGES[data.error_code];
  if (data.outcome && OUTCOME_MESSAGES[data.outcome]) return OUTCOME_MESSAGES[data.outcome];
  if (data.message && /[\u3400-\u9fff]/.test(data.message)) return data.message;
  if (data.status === "requested") return OUTCOME_MESSAGES.SUCCEEDED;
  return "已收到请求状态，请查看申请记录；未确认的结果不会视为成功。";
}
export function apiErrorMessage(status: number, data: Record<string, unknown>): string {
  if (typeof data.error_code === "string" && MESSAGES[data.error_code]) return MESSAGES[data.error_code];
  const detail = typeof data.detail === "string" ? data.detail : typeof data.error === "string" ? data.error : "";
  if (DETAILS[detail]) return DETAILS[detail];
  if (/[\u3400-\u9fff]/.test(detail)) return detail;
  if (typeof data.outcome === "string") return businessMessage(data as { outcome: string });
  return ({ 401: "身份验证失败，请重新登录。", 403: "您无权执行此操作。", 404: "记录不存在或您无权访问。", 409: "状态已发生变化，请刷新后核实。", 422: "输入格式不正确，请检查后重试。", 429: "请求过于频繁，请稍后再试。", 502: "暂时无法连接服务，请稍后重试。", 503: "服务暂时不可用，请查询状态后重试。" } as Record<number, string>)[status] ?? `接口请求失败（${status}），请稍后重试。`;
}
export function errorMessage(error: unknown, fallback = "请求未完成，请查询状态后重试。"): string {
  if (error instanceof Error) {
    return DETAILS[error.message] ?? (/[\u3400-\u9fff]/.test(error.message) ? error.message : fallback);
  }
  return fallback;
}

export function categoryLabel(value: string): string {
  return ({ electronics: "电子产品", clothing: "服装", home: "家居", sports: "运动", beauty: "美妆", books: "图书" } as Record<string, string>)[value.toLowerCase()] ?? value;
}

const TASK_SCOPES: Record<string, string> = {
  product_identity: "商品身份", current_catalog_facts: "当前商品目录事实",
  order_identity: "订单标识", current_owner: "当前订单归属", current_status: "当前订单状态", current_total: "当前订单金额",
  comparison_objects_and_attributes: "对比对象与商品属性", current_inventory: "当前库存",
  max_price: "价格上限", min_rating: "最低评分", category: "商品分类", in_stock: "库存条件",
};
const TASK_ERRORS: Record<string, string> = {
  MISSING_ACCEPTANCE_CONTRACT: "缺少可执行的验收条件",
  DEPENDENCY_EXPIRED: "前置步骤的证据已过期",
  DEPENDENCY_NOT_VERIFIED: "前置步骤尚未通过核验",
  NO_RELEVANT_TOOL_EVIDENCE: "未取得相关工具的事实证据",
  REQUIRED_ENTITIES_MISSING: "缺少要求核实的对象",
  INSUFFICIENT_ENTITIES: "符合条件的对象数量不足",
  ENTITY_NOT_FOUND_OR_NOT_AUTHORIZED: "对象不存在或当前用户无权访问",
  PRICE_LIMIT_EXCEEDED: "商品价格超过预算上限",
  RATING_BELOW_MINIMUM: "商品评分低于要求",
  CATEGORY_MISMATCH: "商品分类不符合要求",
  STOCK_REQUIREMENT_NOT_MET: "库存不符合要求",
};
export function taskScopeLabel(value: string): string {
  return TASK_SCOPES[value] ?? "其他核验项（详见诊断）";
}
export function taskErrorLabel(value: string): string {
  return TASK_ERRORS[value] ?? "核验未通过（详见诊断）";
}

/** 只翻译服务端写入的固定事件前缀，保留申请人原因原文。 */
export function orderHistoryNote(status: string, notes: string): string {
  const prefix = "Return initiated: ";
  return status === "returned" && notes.startsWith(prefix)
    ? `已发起退货申请：${notes.slice(prefix.length)}`
    : notes;
}
