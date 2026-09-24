import { afterEach, describe, expect, it, vi } from "vitest";
import {
  instant,
  pageEnter,
  prefersReducedMotion,
  withMotionPreference,
} from "./motion";

describe("motion", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("在页面入场变体上暴露 hidden/visible 状态", () => {
    expect(pageEnter.hidden).toBeDefined();
    expect(pageEnter.visible).toBeDefined();
  });

  it("用户偏好减少动效时返回 instant 变体", () => {
    expect(withMotionPreference(pageEnter, true)).toBe(instant);
    expect(withMotionPreference(pageEnter, false)).toBe(pageEnter);
  });

  it("matchMedia 不可用时 prefersReducedMotion 返回 false", () => {
    vi.stubGlobal("window", {});
    expect(prefersReducedMotion()).toBe(false);
  });

  it("prefersReducedMotion 反映媒体查询的匹配结果", () => {
    vi.stubGlobal("window", {
      matchMedia: (query: string) => ({
        matches: query.includes("reduce"),
      }),
    });
    expect(prefersReducedMotion()).toBe(true);
  });
});
