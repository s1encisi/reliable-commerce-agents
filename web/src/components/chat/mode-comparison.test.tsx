import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ModeComparison } from "./mode-comparison";
import { api, type CompareResponse, type OrchestrationMode } from "@/lib/api";

const MODES: OrchestrationMode[] = [
  {
    name: "tool",
    label: "工具路由",
    description: "单次 LLM 调用 + 工具循环。",
    capabilities: { streams: true, supports_hitl: true, supports_checkpoints: false, is_graph: false },
    default: true,
  },
  {
    name: "workflow:pre-purchase",
    label: "购前调研（扇出/扇入）",
    description: "扇出到多个专业智能体后再扇入汇总。",
    capabilities: { streams: true, supports_hitl: false, supports_checkpoints: false, is_graph: true },
    default: false,
  },
];

vi.mock("mermaid", () => ({
  default: { initialize: vi.fn(), render: vi.fn(async () => ({ svg: "<svg></svg>" })) },
}));

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ModeComparison", () => {
  it("对话框打开后拉取模式并以可切换标签的形式渲染", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    render(<ModeComparison />);

    fireEvent.click(screen.getByRole("button", { name: /^对比$/ }));

    await waitFor(() => expect(screen.getByText("购前调研（扇出/扇入）")).toBeInTheDocument());
  });

  it("在输入提示词且选中 2 种以上模式之前，「开始对比」按钮保持禁用", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    render(<ModeComparison />);
    fireEvent.click(screen.getByRole("button", { name: /^对比$/ }));
    await waitFor(() => expect(screen.getByText("购前调研（扇出/扇入）")).toBeInTheDocument());

    // 默认只选中 "tool"（唯一标记 default: true 的模式）。
    const runButton = screen.getByRole("button", { name: /开始对比/ });
    expect(runButton).toBeDisabled();

    fireEvent.click(screen.getByText("购前调研（扇出/扇入）"));
    expect(runButton).toBeDisabled(); // 仍然没有输入提示词

    fireEvent.change(screen.getByPlaceholderText(/耳机/), { target: { value: "worth it?" } });
    expect(runButton).not.toBeDisabled();
  });

  it("执行对比并为每种模式渲染一张结果卡片", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    const compareResponse: CompareResponse = {
      message: "worth it?",
      results: [
        {
          mode: "tool",
          label: "工具路由",
          text: "Yes, it's a solid buy.",
          latency_ms: 420,
          agents_involved: ["orchestrator"],
          step_count: 1,
          graph_mermaid: null,
          error: null,
        },
        {
          mode: "workflow:pre-purchase",
          label: "购前调研（扇出/扇入）",
          text: "Stock: 10 units | Price trend: stable",
          latency_ms: 180,
          agents_involved: ["reviews", "stock"],
          step_count: 6,
          graph_mermaid: "graph LR\n  fan_out[fan-out] --> reviews\n  fan_out --> stock\n",
          error: null,
        },
      ],
    };
    vi.spyOn(api, "compareModes").mockResolvedValue(compareResponse);

    render(<ModeComparison />);
    fireEvent.click(screen.getByRole("button", { name: /^对比$/ }));
    await waitFor(() => expect(screen.getByText("购前调研（扇出/扇入）")).toBeInTheDocument());

    fireEvent.click(screen.getByText("购前调研（扇出/扇入）"));
    fireEvent.change(screen.getByPlaceholderText(/耳机/), { target: { value: "worth it?" } });
    fireEvent.click(screen.getByRole("button", { name: /开始对比/ }));

    await waitFor(() => expect(screen.getByText("Yes, it's a solid buy.")).toBeInTheDocument());
    expect(screen.getByText("Stock: 10 units | Price trend: stable")).toBeInTheDocument();
    expect(screen.getByText("420ms")).toBeInTheDocument();
    expect(screen.getByText("180ms")).toBeInTheDocument();
    expect(api.compareModes).toHaveBeenCalledWith("worth it?", ["tool", "workflow:pre-purchase"]);
  });

  it("内联显示某个模式的错误，且不影响其他模式", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    vi.spyOn(api, "compareModes").mockResolvedValue({
      message: "x",
      results: [
        {
          mode: "tool",
          label: "工具路由",
          text: "ok",
          latency_ms: 10,
          agents_involved: [],
          step_count: 0,
          graph_mermaid: null,
          error: null,
        },
        {
          mode: "workflow:pre-purchase",
          label: "购前调研（扇出/扇入）",
          text: "",
          latency_ms: 0,
          agents_involved: [],
          step_count: 0,
          graph_mermaid: null,
          error: "Couldn't find a product matching 'x'.",
        },
      ],
    });

    render(<ModeComparison />);
    fireEvent.click(screen.getByRole("button", { name: /^对比$/ }));
    await waitFor(() => expect(screen.getByText("购前调研（扇出/扇入）")).toBeInTheDocument());
    fireEvent.click(screen.getByText("购前调研（扇出/扇入）"));
    fireEvent.change(screen.getByPlaceholderText(/耳机/), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: /开始对比/ }));

    await waitFor(() => expect(screen.getByText("ok")).toBeInTheDocument());
    expect(screen.getByText("Couldn't find a product matching 'x'.")).toBeInTheDocument();
  });
});
