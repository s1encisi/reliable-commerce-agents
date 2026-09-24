import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DistributionChart } from "./distribution-chart";

/** 为何不断言 recharts 内部实现，见 trend-chart.test.tsx。 */
describe("DistributionChart", () => {
  it("数据为空时展示空态提示，而非空图表", () => {
    render(<DistributionChart data={[]} />);
    expect(screen.getByText("暂无分布数据。")).toBeInTheDocument();
  });

  it("非空数据下挂载图表容器且不崩溃", () => {
    const { container } = render(
      <DistributionChart
        data={[
          { label: "5 star", value: 12 },
          { label: "4 star", value: 8 },
        ]}
      />
    );
    expect(container.querySelector('[data-slot="chart"]')).toBeInTheDocument();
    expect(screen.queryByText("暂无分布数据。")).not.toBeInTheDocument();
  });
});
