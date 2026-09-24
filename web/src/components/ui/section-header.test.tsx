import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SectionHeader } from "./section-header";

describe("SectionHeader", () => {
  it("把标题渲染为 heading", () => {
    render(<SectionHeader title="生效中的测试计划" />);
    expect(
      screen.getByRole("heading", { name: "生效中的测试计划" }),
    ).toBeInTheDocument();
  });

  it("渲染眉标、描述与操作区", () => {
    render(
      <SectionHeader
        eyebrow="你的工作区"
        title="生效中的测试计划"
        description="跟踪各计划的覆盖情况。"
        action={<button type="button">新建计划</button>}
      />,
    );
    expect(screen.getByText("你的工作区")).toBeInTheDocument();
    expect(screen.getByText("跟踪各计划的覆盖情况。")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "新建计划" }),
    ).toBeInTheDocument();
  });
});
