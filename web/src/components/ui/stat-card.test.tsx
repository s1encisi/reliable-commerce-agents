import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Activity } from "lucide-react";
import { StatCard } from "./stat-card";

describe("StatCard", () => {
  it("渲染标签与数值", () => {
    render(<StatCard label="调用次数" value="123.4K" />);
    expect(screen.getByText("调用次数")).toBeInTheDocument();
    expect(screen.getByText("123.4K")).toBeInTheDocument();
  });

  it("渲染带趋势样式的变化量", () => {
    render(
      <StatCard
        label="缺陷数"
        value={11}
        delta={{ value: "本周 +3", trend: "up" }}
      />,
    );
    const delta = screen.getByText("本周 +3");
    expect(delta).toBeInTheDocument();
    expect(delta.className).toContain("text-success");
  });

  it("渲染可选的说明与装饰性图标", () => {
    const { container } = render(
      <StatCard label="Token" value="4.2M" icon={Activity} hint="最近 24 小时" />,
    );
    expect(screen.getByText("最近 24 小时")).toBeInTheDocument();
    // 图标为装饰性元素
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden");
  });
});
