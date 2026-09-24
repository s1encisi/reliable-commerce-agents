import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatSentimentCard } from "./sentiment-card";

describe("ChatSentimentCard", () => {
  it("仅凭 analyze_sentiment 的字段即可渲染（无趋势、无风险）", () => {
    render(
      <ChatSentimentCard
        data={{
          product_name: "Sony WH-1000XM5",
          overall_sentiment: "positive",
          average_rating: 4.7,
          total_reviews: 15,
          rating_distribution: { "5": 10, "4": 3, "3": 1, "2": 1, "1": 0 },
          pros: ["Quality", "Comfortable"],
          cons: ["Expensive"],
        }}
      />
    );
    expect(screen.getByText("Sony WH-1000XM5")).toBeInTheDocument();
    expect(screen.getByText("正面")).toBeInTheDocument();
    expect(screen.getByText("4.7")).toBeInTheDocument();
    expect(screen.getByText("基于 15 条评论")).toBeInTheDocument();
    expect(screen.getByText("Quality")).toBeInTheDocument();
    expect(screen.getByText("Expensive")).toBeInTheDocument();
  });

  it("存在趋势与风险数据时渲染对应徽章", () => {
    render(
      <ChatSentimentCard
        data={{
          product_name: "Sony WH-1000XM5",
          trend: "declining",
          risk_level: "high",
          suspicious_count: 4,
        }}
      />
    );
    expect(screen.getByText("下滑")).toBeInTheDocument();
    expect(screen.getByText("高风险")).toBeInTheDocument();
    expect(screen.getByText(/4 条评论被标记为疑似虚假评论/)).toBeInTheDocument();
  });

  it("仅给出商品名时也能稀疏渲染", () => {
    render(<ChatSentimentCard data={{ product_name: "Sony WH-1000XM5" }} />);
    expect(screen.getByText("Sony WH-1000XM5")).toBeInTheDocument();
  });
});
