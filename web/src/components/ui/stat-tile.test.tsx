import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatTile } from "./stat-tile";

describe("StatTile", () => {
  it("渲染标签与数值", () => {
    render(<StatTile label="库存" value={42} />);
    expect(screen.getByText("库存")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("通过 tone 为数值着色", () => {
    render(<StatTile label="风险等级" value="高" tone="destructive" />);
    expect(screen.getByText("高").className).toContain("text-destructive");
  });

  it("默认不带色调", () => {
    render(<StatTile label="评论数" value={128} />);
    expect(screen.getByText("128").className).not.toContain("text-success");
    expect(screen.getByText("128").className).not.toContain("text-destructive");
  });

  it("渲染可选的说明", () => {
    render(<StatTile label="情感倾向" value="正面" tone="success" hint="最近 30 天" />);
    expect(screen.getByText("最近 30 天")).toBeInTheDocument();
  });
});
