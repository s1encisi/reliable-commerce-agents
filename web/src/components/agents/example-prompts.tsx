"use client";

import Link from "next/link";
import { MessageSquare } from "lucide-react";
import { chatPromptHref } from "@/lib/scenarios";
import type { AgentMeta } from "@/lib/agents";

interface ExamplePromptsProps {
  agent: AgentMeta;
}

/**
 * 示例提问区。
 *
 * 每个标签都是一个直达对话页的链接，点击后自动把该提问预填到输入框，
 * 便于面试官或访客一键复现该智能体的典型用法。
 */
export function ExamplePrompts({ agent }: ExamplePromptsProps) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold text-foreground">
        试一试 · 示例提问
      </h2>
      <div className="flex flex-wrap gap-2">
        {agent.examplePrompts.map((prompt) => (
          <Link
            key={prompt}
            href={chatPromptHref(prompt)}
            className="inline-flex items-center gap-1.5 rounded-full border bg-card px-3 py-1.5 text-sm text-foreground/80 transition-colors hover:border-primary/40 hover:bg-accent hover:text-foreground"
          >
            <MessageSquare className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            {prompt}
          </Link>
        ))}
      </div>
    </section>
  );
}
