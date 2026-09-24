import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

/**
 * `chatStream()` 的 SSE 解析器必须把结构化的非文本帧（非「tool」编排模式
 * 发出的 `node`/`handoff`/`checkpoint`/`request_info`/`error` 事件——见
 * `orchestrator/routes/chat.py`）路由到 `onOrchestrationEvent`，而不是
 * `onChunk`——若落到 `onChunk`，它们的原始 JSON 载荷会被当作助手可见回复
 * 的一部分渲染出来。`step`/`metadata`/纯文本的行为必须与之前完全一致。
 * `delta` 帧（专业智能体自身的实时预览）路由到它自己的 `onDeltaChunk`
 * 回调，而不是 `onChunk`（第 8.1 阶段）——见下方第二个测试。
 */

function fakeStreamResponse(rawBody: string) {
  const encoder = new TextEncoder();
  let sent = false;
  return {
    status: 200,
    ok: true,
    body: {
      getReader() {
        return {
          async read() {
            if (sent) return { done: true, value: undefined };
            sent = true;
            return { done: false, value: encoder.encode(rawBody) };
          },
          releaseLock() {},
        };
      },
    },
    json: async () => ({}),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("chatStream 的 SSE 解析", () => {
  it("把结构化帧路由到 onOrchestrationEvent 而非 onChunk，并保持 step/metadata/文本不变", async () => {
    const raw = [
      "data: Hello ",
      "",
      'event: node\ndata: {"node_id":"math","phase":"enter"}',
      "",
      'event: step\ndata: {"tool_name":"lookup","status":"success"}',
      "",
      "data:  world",
      "",
      'event: metadata\ndata: {"conversation_id":"c1","agents_involved":["orchestrator","math"]}',
      "",
      "data: [DONE]",
      "",
    ].join("\n");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(fakeStreamResponse(raw))
    );

    const chunks: string[] = [];
    const orchestrationEvents: Array<{ name: string; data: unknown }> = [];
    const steps: unknown[] = [];

    const metadata = await api.chatStream(
      "hi",
      undefined,
      (chunk) => chunks.push(chunk),
      undefined,
      {
        onStep: (step) => steps.push(step),
        onOrchestrationEvent: (name, data) => orchestrationEvents.push({ name, data }),
      }
    );

    expect(chunks).toEqual(["Hello ", " world"]);
    expect(chunks.join("")).not.toContain("node_id");

    expect(orchestrationEvents).toEqual([{ name: "node", data: { node_id: "math", phase: "enter" } }]);

    expect(steps).toEqual([{ tool_name: "lookup", status: "success" }]);

    expect(metadata).toEqual({ conversation_id: "c1", agents_involved: ["orchestrator", "math"] });
  });

  it("把 `delta` 帧路由到 onDeltaChunk 而非 onChunk（第 8.1 阶段——不再重复持久化）", async () => {
    const raw = ["event: delta\ndata: specialist chunk", "", "data: [DONE]", ""].join("\n");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(fakeStreamResponse(raw))
    );

    const chunks: string[] = [];
    const deltaChunks: string[] = [];
    await api.chatStream("hi", undefined, (chunk) => chunks.push(chunk), undefined, {
      onDeltaChunk: (chunk) => deltaChunks.push(chunk),
    });

    // `delta` 帧是专业智能体的实时预览，不是最终回答——它绝不能进入
    // onChunk 写入的同一个缓冲区（正是这一点导致每个调用过专业智能体的
    // tool 模式回答被渲染两次：两段各自措辞的复述，外加重复的卡片代码块）。
    // 见 orchestrator/routes/chat.py 的流式消费循环。
    expect(chunks).toEqual([]);
    expect(deltaChunks).toEqual(["specialist chunk"]);
  });
});
