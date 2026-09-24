import { describe, expect, it } from "vitest";
import {
  DataTableDataSchema,
  DistributionChartDataSchema,
  StatTileDataSchema,
  TrendChartDataSchema,
} from "./chat-schemas";

describe("生成式 UI 基础类型的 schema（第 8.4 阶段 Step 3）", () => {
  it("DataTableDataSchema 接受格式正确的表格", () => {
    const result = DataTableDataSchema.safeParse({
      title: "Warehouse stock",
      columns: [
        { key: "name", header: "Warehouse" },
        { key: "stock", header: "Stock", align: "right" },
      ],
      rows: [{ name: "East", stock: 42 }],
    });
    expect(result.success).toBe(true);
  });

  it("DataTableDataSchema 拒绝超过 50 行", () => {
    const rows = Array.from({ length: 51 }, (_, i) => ({ name: `Row ${i}`, stock: i }));
    const result = DataTableDataSchema.safeParse({
      columns: [{ key: "name", header: "Name" }],
      rows,
    });
    expect(result.success).toBe(false);
  });

  it("TrendChartDataSchema 接受时间序列", () => {
    const result = TrendChartDataSchema.safeParse({
      xKey: "month",
      series: [{ key: "rating", label: "Average rating" }],
      data: [
        { month: "Jan", rating: 4.2 },
        { month: "Feb", rating: 4.5 },
      ],
    });
    expect(result.success).toBe(true);
  });

  it("DistributionChartDataSchema 接受 label/value 形式的分布", () => {
    const result = DistributionChartDataSchema.safeParse({
      data: [
        { label: "5 star", value: 12 },
        { label: "4 star", value: 8 },
      ],
    });
    expect(result.success).toBe(true);
  });

  it("StatTileDataSchema 接受带 tone 的标量", () => {
    const result = StatTileDataSchema.safeParse({
      label: "Risk Level",
      value: "High",
      tone: "destructive",
    });
    expect(result.success).toBe(true);
  });

  it("StatTileDataSchema 拒绝无法识别的 tone", () => {
    const result = StatTileDataSchema.safeParse({
      label: "Risk Level",
      value: "High",
      tone: "extreme",
    });
    expect(result.success).toBe(false);
  });
});
