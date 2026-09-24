import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AgentTimeline } from "./agent-timeline";
import type { AgentStep } from "@/lib/api";

const STEPS: AgentStep[] = [
  {
    agent: "orchestrator",
    tool_name: "call_specialist_agent",
    tool_input: { agent_name: "product-discovery" },
    tool_output: { response: "Found 3 items" },
    status: "success",
    duration_ms: 120,
  },
  {
    agent: "product-discovery",
    tool_name: "search_products",
    tool_input: { query: "headphones", max_price: 300 },
    tool_output: { count: 3 },
    status: "success",
    duration_ms: 89,
  },
  {
    agent: "product-discovery",
    tool_name: "check_stock",
    tool_input: { product_id: "abc" },
    tool_output: null,
    status: "error",
    duration_ms: 12,
  },
];

describe("AgentTimeline", () => {
  it("steps 数组为空时不渲染任何内容", () => {
    const { container } = render(<AgentTimeline steps={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("默认展开步骤行 —— 时间线本身就是重点，不该藏在一次点击之后", () => {
    render(<AgentTimeline steps={STEPS} />);
    expect(screen.getByText(/智能体活动 · 3 步/)).toBeInTheDocument();
    expect(screen.getByText("search_products")).toBeInTheDocument();
    expect(screen.getByText("call_specialist_agent")).toBeInTheDocument();
    expect(screen.getByText("check_stock")).toBeInTheDocument();
  });

  it("在标题栏展示总耗时", () => {
    render(<AgentTimeline steps={STEPS} />);
    // 120 + 89 + 12 = 221ms
    expect(screen.getByText("共 221ms")).toBeInTheDocument();
  });

  it("点击后折叠以隐藏步骤行", () => {
    render(<AgentTimeline steps={STEPS} />);
    fireEvent.click(screen.getByRole("button", { name: /智能体活动/ }));
    expect(screen.queryByText("search_products")).not.toBeInTheDocument();
  });

  it("在每一行展示智能体标签", () => {
    render(<AgentTimeline steps={STEPS} />);
    expect(screen.getAllByText("orchestrator").length).toBeGreaterThan(0);
    expect(screen.getAllByText("product-discovery").length).toBeGreaterThan(0);
  });

  it("展示每个步骤的耗时", () => {
    render(<AgentTimeline steps={STEPS} />);
    expect(screen.getByText("120ms")).toBeInTheDocument();
    expect(screen.getByText("89ms")).toBeInTheDocument();
  });

  it("展开步骤行可看到 tool_input 与 tool_output", () => {
    render(<AgentTimeline steps={STEPS} />);
    // 点击 search_products 这一行以展开
    fireEvent.click(screen.getByText("search_products").closest("button")!);
    expect(screen.getByText("输入")).toBeInTheDocument();
    expect(screen.getByText("输出")).toBeInTheDocument();
    // tool_input 的 JSON 应当可见
    expect(screen.getByText(/headphones/)).toBeInTheDocument();
  });

  it("对没有 tool_input 或 tool_output 的步骤不显示展开箭头", () => {
    const noDetailStep: AgentStep[] = [
      { agent: "orchestrator", tool_name: "noop", status: "success", duration_ms: 1 },
    ];
    render(<AgentTimeline steps={noDetailStep} />);
    // 该步骤按钮应为禁用状态（没有可展开的详情）
    const stepBtn = screen.getByText("noop").closest("button")!;
    expect(stepBtn).toBeDisabled();
  });

  it("agent 字段缺失时回退为 'orchestrator'", () => {
    const steps: AgentStep[] = [
      { tool_name: "some_tool", status: "success", duration_ms: 5 },
    ];
    render(<AgentTimeline steps={steps} />);
    expect(screen.getByText("orchestrator")).toBeInTheDocument();
  });

  it("当步骤展开且 provenance 带行记录时，展示带行 id 的「数据来源」行", () => {
    const steps: AgentStep[] = [
      {
        agent: "product-discovery",
        tool_name: "search_products",
        tool_input: { query: "headphones" },
        tool_output: { count: 1 },
        status: "success",
        duration_ms: 50,
        provenance: { source: "tool:search_products", row_ids: ["0fd372fa-ecb2-4db0-bb71-8628a784ced9"] },
      },
    ];
    render(<AgentTimeline steps={steps} />);
    fireEvent.click(screen.getByText("search_products").closest("button")!);
    expect(screen.getByText("tool:search_products")).toBeInTheDocument();
    expect(screen.getByText(/0fd372fa-ecb2-4db0-bb71-8628a784ced9/)).toBeInTheDocument();
  });

  it("provenance 没有行记录时省略「数据来源」行", () => {
    const steps: AgentStep[] = [
      {
        agent: "product-discovery",
        tool_name: "get_trending_products",
        tool_input: {},
        tool_output: [],
        status: "success",
        duration_ms: 40,
        provenance: { source: "tool:get_trending_products", row_ids: [] },
      },
    ];
    render(<AgentTimeline steps={steps} />);
    fireEvent.click(screen.getByText("get_trending_products").closest("button")!);
    expect(screen.queryByText(/数据来源/)).not.toBeInTheDocument();
  });
});
