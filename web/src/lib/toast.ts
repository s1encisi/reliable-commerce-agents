/**
 * 基于 sonner 构建的应用级 toast 辅助函数。
 *
 * 需要任意 toast 时导入 { toast }；需要全应用统一的提示文案时，
 * 使用下面这些具名辅助函数。
 */
export { toast } from "sonner";

import { toast } from "sonner";

// ─── 购物车 ──────────────────────────────────────────────────────────────────

export function toastCartAdded(productName: string) {
  toast.success(`已加入购物车`, { description: productName });
}

export function toastCartRemoved(productName: string) {
  toast(`已从购物车移除`, { description: productName });
}

export function toastCartUpdated() {
  toast.success("购物车已更新");
}

export function toastCouponApplied(code: string) {
  toast.success(`优惠券已使用`, { description: code });
}

export function toastCouponFailed(reason: string) {
  toast.error("优惠券未生效", { description: reason });
}

// ─── 订单 ────────────────────────────────────────────────────────────────────

export function toastOrderPlaced(orderId: string) {
  toast.success("下单成功", {
    description: `订单 #${orderId.slice(0, 8)} 已确认。`,
  });
}

export function toastOrderCancelled(orderId: string) {
  toast(`订单已取消`, {
    description: `订单 #${orderId.slice(0, 8)} 已取消。`,
  });
}

export function toastOrderModified() {
  toast.success("订单已更新");
}

// ─── 退货 ────────────────────────────────────────────────────────────────────

export function toastReturnInitiated(returnId: string) {
  toast.success("已发起退货", {
    description: `退货单 #${returnId.slice(0, 8)} 已创建。可在订单详情中查看面单。`,
  });
}

// ─── 登录 / 账户 ─────────────────────────────────────────────────────────────

export function toastProfileSaved() {
  toast.success("个人资料已保存");
}

export function toastAddressSaved() {
  toast.success("收货地址已保存");
}

// ─── 智能体市场 ──────────────────────────────────────────────────────────────

export function toastAccessRequested(agentName: string) {
  toast.success("已提交权限申请", {
    description: `您对 ${agentName} 的申请正在等待管理员审批。`,
  });
}

export function toastAccessApproved(agentName: string) {
  toast.success("权限已通过", { description: agentName });
}

// ─── 通用 ────────────────────────────────────────────────────────────────────

export function toastCopied(what: string) {
  toast(`${what} 已复制到剪贴板`);
}

export function toastError(message: string) {
  toast.error(message);
}
