import { describe, expect, it } from "vitest";
import {
  DEMO_SCENARIOS,
  QUICK_PROMPTS,
  chatPromptHref,
  shopAssistantHref,
} from "./scenarios";

describe("DEMO_SCENARIOS", () => {
  it("恰好包含 8 个场景", () => {
    expect(DEMO_SCENARIOS).toHaveLength(8);
  });

  it("每个场景的 id、label、description、prompt 均非空，且至少指定一个智能体", () => {
    for (const s of DEMO_SCENARIOS) {
      expect(s.id, `${s.label} 缺少 id`).toBeTruthy();
      expect(s.label, `${s.id} 缺少 label`).toBeTruthy();
      expect(s.description, `${s.id} 缺少 description`).toBeTruthy();
      expect(s.prompt, `${s.id} 缺少 prompt`).toBeTruthy();
      expect(s.agents.length, `${s.id} 必须至少指定一个智能体`).toBeGreaterThan(0);
      expect(s.icon, `${s.id} 缺少 icon`).toBeTruthy();
    }
  });

  it("id 唯一", () => {
    const ids = DEMO_SCENARIOS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("QUICK_PROMPTS", () => {
  it("把前 4 个场景以 label/prompt 简单对的形式暴露", () => {
    expect(QUICK_PROMPTS).toHaveLength(4);
    for (let i = 0; i < 4; i++) {
      expect(QUICK_PROMPTS[i].label).toBe(DEMO_SCENARIOS[i].label);
      expect(QUICK_PROMPTS[i].prompt).toBe(DEMO_SCENARIOS[i].prompt);
    }
  });
});

describe("chatPromptHref", () => {
  it("构建经过编码的对话深链", () => {
    expect(chatPromptHref("find a gift & deal")).toBe(
      "/chat?prompt=find%20a%20gift%20%26%20deal",
    );
  });
});

describe("shopAssistantHref", () => {
  it("构建经过编码的公开助手深链", () => {
    expect(shopAssistantHref("check stock")).toBe(
      "/shop/assistant?prompt=check%20stock",
    );
  });
});
