import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CheckCircle } from "lucide-react";
import { StatusBadge } from "./status-badge";

describe("StatusBadge", () => {
  it("用色调对应的令牌类渲染标签", () => {
    render(<StatusBadge label="有货" tone="success" />);
    const badge = screen.getByText("有货");
    expect(badge.className).toContain("text-success");
  });

  it.each([
    ["success", "text-success"],
    ["warning", "text-warning"],
    ["info", "text-info"],
    ["destructive", "text-destructive"],
    ["neutral", "text-muted-foreground"],
  ] as const)("tone=%s 映射到 %s", (tone, expectedClass) => {
    render(<StatusBadge label="状态" tone={tone} />);
    expect(screen.getByText("状态").className).toContain(expectedClass);
  });

  it("渲染可选的图标", () => {
    const { container } = render(<StatusBadge label="已送达" tone="success" icon={CheckCircle} />);
    expect(container.querySelector("svg")).toBeInTheDocument();
  });
});
