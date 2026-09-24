import { AGENTS } from "@/lib/agents";
import { AgentDetailClient } from "./agent-detail-client";

export function generateStaticParams() {
  return AGENTS.map((a) => ({ slug: a.slug }));
}

// Next.js 16：params 是 Promise —— 必须 await
export default async function AgentDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <AgentDetailClient slug={slug} />;
}
