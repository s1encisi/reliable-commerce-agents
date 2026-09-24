import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DataTable } from "./data-table";

interface Row {
  name: string;
  stock: number;
  [key: string]: unknown;
}

describe("DataTable", () => {
  const columns = [
    { key: "name", header: "仓库" },
    { key: "stock", header: "库存", align: "right" as const },
  ];
  const rows: Row[] = [
    { name: "华东", stock: 42 },
    { name: "华西", stock: 0 },
  ];

  it("渲染表头与单元格值", () => {
    render(<DataTable columns={columns} rows={rows} />);
    expect(screen.getByText("仓库")).toBeInTheDocument();
    expect(screen.getByText("库存")).toBeInTheDocument();
    expect(screen.getByText("华东")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("华西")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("提供自定义 render 函数时使用它", () => {
    render(
      <DataTable
        columns={[
          { key: "name", header: "仓库" },
          { key: "stock", header: "库存", render: (r) => (r.stock > 0 ? "有货" : "缺货") },
        ]}
        rows={rows}
      />
    );
    expect(screen.getByText("有货")).toBeInTheDocument();
    expect(screen.getByText("缺货")).toBeInTheDocument();
  });

  it("无数据时展示空态文案，而非空表格", () => {
    render(<DataTable columns={columns} rows={[]} emptyMessage="这里空空如也" />);
    expect(screen.getByText("这里空空如也")).toBeInTheDocument();
    expect(screen.queryByText("仓库")).not.toBeInTheDocument();
  });

  it("渲染可选的表格说明", () => {
    render(<DataTable columns={columns} rows={rows} caption="各区域库存" />);
    expect(screen.getByText("各区域库存")).toBeInTheDocument();
  });
});
