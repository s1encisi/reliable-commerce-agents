import { describe, expect, it, vi, afterEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { OrchestrationGraph } from "./orchestration-graph";
import { api } from "@/lib/api";

const SAMPLE_GRAPH =
  "graph LR\n" +
  "  fan_out[fan-out] --> reviews\n" +
  "  fan_out --> stock\n" +
  "  reviews --> merge_and_ship[merge-and-ship]\n" +
  "  stock --> merge_and_ship\n";

vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (_id: string, source: string) => ({ svg: `<svg data-source="${encodeURIComponent(source)}"></svg>` })),
  },
}));

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OrchestrationGraph", () => {
  it("没有固定图的模式（mermaid: null）不渲染任何内容", async () => {
    vi.spyOn(api, "getModeGraph").mockResolvedValue({ name: "tool", mermaid: null });
    const { container } = render(<OrchestrationGraph mode="tool" />);
    await waitFor(() => expect(api.getModeGraph).toHaveBeenCalledWith("tool"));
    expect(container.firstChild).toBeNull();
  });

  it("图请求仍在进行或失败时不渲染任何内容", async () => {
    vi.spyOn(api, "getModeGraph").mockRejectedValue(new Error("network error"));
    const { container } = render(<OrchestrationGraph mode="workflow:pre-purchase" />);
    await waitFor(() => expect(api.getModeGraph).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("图加载完成后渲染出容器", async () => {
    vi.spyOn(api, "getModeGraph").mockResolvedValue({ name: "workflow:pre-purchase", mermaid: SAMPLE_GRAPH });
    const { container } = render(<OrchestrationGraph mode="workflow:pre-purchase" />);
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
  });

  it("把激活的节点 id（连字符形式）标记为 mermaid 的 active 类", async () => {
    vi.spyOn(api, "getModeGraph").mockResolvedValue({ name: "workflow:pre-purchase", mermaid: SAMPLE_GRAPH });
    const { container } = render(<OrchestrationGraph mode="workflow:pre-purchase" activeNodeIds={["fan-out"]} />);
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
    const encoded = container.querySelector("svg")!.getAttribute("data-source")!;
    const source = decodeURIComponent(encoded);
    expect(source).toContain("class fan_out active");
  });

  it("把已完成的节点 id 标记为 success，未触及的节点默认为 core", async () => {
    vi.spyOn(api, "getModeGraph").mockResolvedValue({ name: "workflow:pre-purchase", mermaid: SAMPLE_GRAPH });
    const { container } = render(<OrchestrationGraph mode="workflow:pre-purchase" doneNodeIds={["fan-out"]} />);
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
    const source = decodeURIComponent(container.querySelector("svg")!.getAttribute("data-source")!);
    expect(source).toContain("class fan_out success");
    expect(source).toContain("class reviews core");
  });

  it("mode 属性变化时重新拉取图", async () => {
    vi.spyOn(api, "getModeGraph").mockResolvedValue({ name: "workflow:pre-purchase", mermaid: SAMPLE_GRAPH });
    const { rerender } = render(<OrchestrationGraph mode="workflow:pre-purchase" />);
    await waitFor(() => expect(api.getModeGraph).toHaveBeenCalledWith("workflow:pre-purchase"));

    rerender(<OrchestrationGraph mode="workflow:return-replace" />);
    await waitFor(() => expect(api.getModeGraph).toHaveBeenCalledWith("workflow:return-replace"));
  });
});
