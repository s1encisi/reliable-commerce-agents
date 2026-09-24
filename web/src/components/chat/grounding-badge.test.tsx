import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GroundingBadge } from "./grounding-badge";
import type { GroundingReport } from "@/lib/api";

const ALL_VERIFIED: GroundingReport = {
  total: 2,
  verified: 2,
  unverified: 0,
  claims: [
    { type: "product", id: "0fd372fa-ecb2-4db0-bb71-8628a784ced9", status: "verified", detail: null, source: "ledger" },
    { type: "order", id: "1a2b3c4d-5e6f-7890-abcd-ef0123456789", status: "verified", detail: null, source: "db" },
  ],
};

const MIXED: GroundingReport = {
  total: 2,
  verified: 1,
  unverified: 1,
  claims: [
    { type: "product", id: "0fd372fa-ecb2-4db0-bb71-8628a784ced9", status: "verified", detail: null, source: "ledger" },
    {
      type: "product",
      id: "99999999-9999-9999-9999-999999999999",
      status: "not_found",
      detail: "不存在使用该 id 的商品",
      source: "db",
    },
  ],
};

describe("GroundingBadge", () => {
  it("没有报告时不渲染任何内容", () => {
    const { container } = render(<GroundingBadge report={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("报告中没有待核验声明时不渲染任何内容", () => {
    const { container } = render(<GroundingBadge report={{ total: 0, verified: 0, unverified: 0, claims: [] }} />);
    expect(container.firstChild).toBeNull();
  });

  it("在折叠摘要中展示已核验数量", () => {
    render(<GroundingBadge report={ALL_VERIFIED} />);
    expect(screen.getByText(/已对照数据库核验 2 条事实/)).toBeInTheDocument();
    expect(screen.queryByText(/无法核验/)).not.toBeInTheDocument();
  });

  it("部分声明核验失败时提示无法核验的数量", () => {
    render(<GroundingBadge report={MIXED} />);
    expect(screen.getByText(/已对照数据库核验 1 条事实，1 条无法核验/)).toBeInTheDocument();
  });

  it("点击后展开展示每条声明的详情", () => {
    render(<GroundingBadge report={MIXED} />);
    expect(screen.queryByText("不存在使用该 id 的商品")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("不存在使用该 id 的商品")).toBeInTheDocument();
    expect(screen.getByText("未找到 — 已移除")).toBeInTheDocument();
    expect(screen.getByText("已核验")).toBeInTheDocument();
  });
});
