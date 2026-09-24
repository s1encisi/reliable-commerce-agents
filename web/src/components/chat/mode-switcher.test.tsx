import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ModeSwitcher } from "./mode-switcher";
import { api, type OrchestrationMode } from "@/lib/api";

const MODES: OrchestrationMode[] = [
  {
    name: "tool",
    label: "工具路由",
    description: "编排器 LLM 通过 call_specialist_agent 路由到专业智能体。",
    capabilities: { streams: true, supports_hitl: true, supports_checkpoints: false, is_graph: false },
    default: true,
  },
  {
    name: "workflow:return-replace",
    label: "退货与换货（顺序执行 + 工作流内人工参与）",
    description: "顺序执行的工作流，内置人工参与（HITL）关卡。",
    capabilities: { streams: true, supports_hitl: true, supports_checkpoints: true, is_graph: true },
    default: false,
  },
];

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ModeSwitcher", () => {
  it("模式加载前不渲染任何内容，请求失败时也保持为空", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockRejectedValue(new Error("network error"));
    const { container } = render(<ModeSwitcher value="" onChange={() => {}} />);
    await waitFor(() => expect(container.firstChild).toBeNull());
  });

  it("模式加载完成后显示所选模式的 label", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    render(<ModeSwitcher value="tool" onChange={() => {}} />);
    await waitFor(() => expect(screen.getByText("工具路由")).toBeInTheDocument());
  });

  it("尚未选择模式时显示「编排模式」占位文本，而不是空白触发器", async () => {
    // 回归测试：base-ui 的 SelectValue 一旦 `children` 是渲染函数，其
    // `placeholder` 属性就会被忽略——这是在浏览器实测中发现的：空 `value`
    // 渲染出的是一个空白触发器，而不是「编排模式」。
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    render(<ModeSwitcher value="" onChange={() => {}} />);
    await waitFor(() => expect(screen.getByText("编排模式")).toBeInTheDocument());
  });

  it("为具备 HITL + 检查点 + 图能力的模式显示能力徽章", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    render(<ModeSwitcher value="workflow:return-replace" onChange={() => {}} />);
    await waitFor(() => expect(screen.getByText("图")).toBeInTheDocument());
    expect(screen.getByText("HITL")).toBeInTheDocument();
    expect(screen.getByText("检查点")).toBeInTheDocument();
  });

  it("纯工具模式不显示任何能力徽章", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    render(<ModeSwitcher value="tool" onChange={() => {}} />);
    await waitFor(() => expect(screen.getByText("工具路由")).toBeInTheDocument());
    expect(screen.queryByText("图")).not.toBeInTheDocument();
    expect(screen.queryByText("检查点")).not.toBeInTheDocument();
  });
});
