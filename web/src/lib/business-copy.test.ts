import { describe, it, expect } from "vitest";
import { apiErrorMessage, businessMessage, errorMessage, refundMethodLabel, returnStatusLabel, taskScopeLabel, taskErrorLabel, orderHistoryNote } from "./business-copy";

describe("业务中文展示", () => {
  it("审批与创建不夸大为退款完成", () => {
    expect(businessMessage({ outcome: "AWAITING_APPROVAL", message: "English message" })).toContain("尚未创建退货或发放退款");
    expect(businessMessage({ outcome: "SUCCEEDED" })).toBe("退货申请已创建，尚未发放退款。");
  });
  it("任务业务反馈使用中文，原始协议留在诊断详情", () => {
    expect(taskScopeLabel("product_identity")).toBe("商品身份");
    expect(taskScopeLabel("current_catalog_facts")).toBe("当前商品目录事实");
    expect(taskErrorLabel("PRICE_LIMIT_EXCEEDED")).toBe("商品价格超过预算上限");
    expect(taskErrorLabel("FUTURE_CODE")).toBe("核验未通过（详见诊断）");
  });
  it("退货时间线只转换系统固定前缀，完整保留混合语言原因", () => {
    const reason = "外壳破损 / Left hinge broken; quoted Return initiated: original text";
    expect(orderHistoryNote("returned", `Return initiated: ${reason}`)).toBe(`已发起退货申请：${reason}`);
  });
  it("不改写其他历史事件或已经中文化的原始备注", () => {
    expect(orderHistoryNote("delivered", "Return initiated: 用户引用")).toBe("Return initiated: 用户引用");
    expect(orderHistoryNote("returned", "已发起退货申请：商品破损")).toBe("已发起退货申请：商品破损");
  });
  it("保留复核、拒绝和未知结果的差别", () => {
    expect(businessMessage({ error_code: "DELIVERY_TIME_MISSING", outcome: "NEEDS_REVIEW" })).toContain("人工核实");
    expect(businessMessage({ error_code: "RETURN_WINDOW_EXPIRED", outcome: "REJECTED" })).toContain("30 天");
    expect(businessMessage({ outcome: "UNKNOWN" })).toContain("尚未确认");
  });
  it("不把原始枚举或未知技术异常直接作为业务状态", () => {
    expect(refundMethodLabel("original_payment")).toBe("原路退回");
    expect(returnStatusLabel("requested")).toBe("已创建申请");
    expect(returnStatusLabel("new_server_status")).toBe("状态待核实");
    expect(errorMessage(new TypeError("Failed to fetch"))).toContain("查询状态");
    expect(apiErrorMessage(403, { detail: "Account is deactivated" })).toContain("账号已停用");
  });
});
