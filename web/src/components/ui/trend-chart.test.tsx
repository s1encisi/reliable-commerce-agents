import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TrendChart } from "./trend-chart";

/**
 * recharts 的 ResponsiveContainer 只有在通过 ResizeObserver 量到非零尺寸后
 * 才会渲染子元素——jsdom 从不提供真实布局，因此这些测试覆盖空态分支（纯条件
 * 判断），并确认非空分支能挂载图表容器而不崩溃，而不是去断言 recharts 在此
 * 环境下根本不会产出的内部 SVG 内容。
 */
describe("TrendChart", () => {
  it("数据为空时展示空态提示，而非空图表", () => {
    render(<TrendChart data={[]} xKey="month" series={[{ key: "rating", label: "Rating" }]} />);
    expect(screen.getByText("暂无趋势数据。")).toBeInTheDocument();
  });

  it("非空数据下挂载图表容器且不崩溃", () => {
    const { container } = render(
      <TrendChart
        data={[
          { month: "Jan", rating: 4.2 },
          { month: "Feb", rating: 4.5 },
        ]}
        xKey="month"
        series={[{ key: "rating", label: "Rating" }]}
      />
    );
    expect(container.querySelector('[data-slot="chart"]')).toBeInTheDocument();
    expect(screen.queryByText("暂无趋势数据。")).not.toBeInTheDocument();
  });
});
