import { z } from "zod";

/**
 * 退货操作的结果契约。
 *
 * `outcome` 是后端返回的机器枚举值，必须保持英文原样；前端只据此决定
 * 展示与后续跳转，不参与翻译。
 */
export const returnOperationSchema = z.object({
  operation_id: z.string().uuid().optional(),
  return_id: z.string().uuid().optional(),
  order_id: z.string().uuid().optional(),
  outcome: z.enum(["READY", "NEEDS_INPUT", "NEEDS_REVIEW", "AWAITING_APPROVAL", "SUCCEEDED", "REJECTED", "RETRYABLE_FAILURE", "UNKNOWN"]).optional(),
  status: z.string().optional(),
  success: z.boolean().optional(),
  message: z.string().optional(),
  error_code: z.string().optional(),
  request_id: z.string().uuid().optional(),
  refund_method: z.string().optional(),
  refund_amount: z.number().nonnegative().optional(),
  return_label_url: z.string().optional(),
});

export type ReturnOperation = z.infer<typeof returnOperationSchema>;
