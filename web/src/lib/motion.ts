/**
 * 应用外壳与页面共用的 framer-motion 变体。
 *
 * 过渡保持短促、有物理感——这是一个效率/购物界面，而不是营销开屏页。
 * 所有时长单位均为秒。
 *
 * 尊重「减少动态效果」偏好：组件应读取 {@link prefersReducedMotion}
 * （或 framer-motion 的 `useReducedMotion`），并在用户选择关闭动效时传入
 * {@link instant} 变体 / `transition={{ duration: 0 }}`。
 */
import type { Transition, Variants } from "framer-motion";

/** 整个外壳统一使用的标准缓动曲线（接近 ease-out）。 */
export const EASE_OUT: Transition["ease"] = [0.16, 1, 0.3, 1];

export const DURATION = {
  fast: 0.15,
  base: 0.22,
  slow: 0.35,
} as const;

/** 页面 / 路由内容的入场动画。 */
export const pageEnter: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: DURATION.base, ease: EASE_OUT },
  },
};

/** 让子项依次延迟出现的容器（配合 {@link listItem} 使用）。 */
export const listStagger: Variants = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.05, delayChildren: 0.02 },
  },
};

/** {@link listStagger} 容器中的子项。 */
export const listItem: Variants = {
  hidden: { opacity: 0, y: 6 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: DURATION.fast, ease: EASE_OUT },
  },
};

/** 卡片悬停时的轻微上浮。 */
export const cardHover = {
  rest: { y: 0 },
  hover: { y: -2, transition: { duration: DURATION.fast, ease: EASE_OUT } },
} satisfies Variants;

/** 用于流式输出 / 进行中指示器的柔和脉动。 */
export const streamPulse: Variants = {
  animate: {
    opacity: [0.4, 1, 0.4],
    transition: { duration: 1.2, repeat: Infinity, ease: "easeInOut" },
  },
};

/** 零动效变体集合，在用户偏好减少动态效果时返回。 */
export const instant: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0 } },
};

/**
 * 对服务端渲染安全的「减少动态效果」检查。在服务端返回 `false`
 * （没有 matchMedia），以保证首屏渲染一致；在客户端组件中需要响应式
 * 场景时，请配合 framer-motion 的 `useReducedMotion` 使用。
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * 根据用户的动效偏好选择合适的变体。
 * @param variants 带动画的变体集合
 * @param reduced 用户是否偏好减少动态效果
 */
export function withMotionPreference(
  variants: Variants,
  reduced: boolean,
): Variants {
  return reduced ? instant : variants;
}
