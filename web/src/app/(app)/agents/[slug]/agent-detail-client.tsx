"use client";

import { useEffect, useState } from "react";
import { notFound } from "next/navigation";
import { motion, useReducedMotion } from "framer-motion";
import { getAgent } from "@/lib/agents";
import { api } from "@/lib/api";
import { pageEnter, instant } from "@/lib/motion";
import { AgentHero } from "@/components/agents/agent-hero";
import { ToolGrid } from "@/components/agents/tool-grid";
import { ExamplePrompts } from "@/components/agents/example-prompts";
import { AgentStatsStrip } from "@/components/agents/agent-stats-strip";

/** 单个智能体的运行时统计，对应后端 `getAgentStats()` 返回的一行。 */
type AgentStat = {
  agent_name: string;
  request_count: number;
  avg_duration_ms: number;
  total_tokens: number;
};

interface Props {
  slug: string;
}

/**
 * 智能体详情页的客户端外壳。
 *
 * 负责拉取该智能体的运行时统计（请求数、平均耗时、Token 消耗），并把
 * 首屏区块组装起来；静态资料（简介、工具清单、示例提问）来自 `@/lib/agents`，
 * 不依赖网络。
 */
export function AgentDetailClient({ slug }: Props) {
  const reduce = useReducedMotion();
  const [stats, setStats] = useState<AgentStat[] | null>(null);

  const agent = getAgent(slug);

  useEffect(() => {
    api
      .getAgentStats()
      .then((rows) => setStats(rows ?? []))
      .catch(() => setStats([]));
  }, []);

  if (!agent) {
    notFound();
  }

  return (
    <motion.div
      variants={reduce ? instant : pageEnter}
      initial="hidden"
      animate="visible"
      className="mx-auto max-w-4xl space-y-10 px-4 py-8 sm:px-6 lg:px-8"
    >
      <AgentHero agent={agent} />

      <AgentStatsStrip backendName={agent.backendName} stats={stats} />

      <ExamplePrompts agent={agent} />

      <ToolGrid agent={agent} />
    </motion.div>
  );
}
