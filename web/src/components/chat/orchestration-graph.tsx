"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

// 项目统一的 Mermaid 配色——tutorials/_shared/mermaid-style-guide.md。
// `core` 是默认（未访问）的节点状态；`active`/`success`/`error` 是随着
// SSE 事件到达、按节点 id 实时叠加的状态。
const MERMAID_INIT = `%%{init: {'theme':'base', 'themeVariables': {
  'primaryColor': '#2563eb',
  'primaryTextColor': '#ffffff',
  'primaryBorderColor': '#1e40af',
  'lineColor': '#64748b',
  'secondaryColor': '#f59e0b',
  'tertiaryColor': '#10b981',
  'background': 'transparent'
}}}%%`;

const CLASS_DEFS = [
  "classDef core fill:#2563eb,stroke:#1e40af,color:#ffffff",
  "classDef active fill:#f59e0b,stroke:#b45309,color:#000000",
  "classDef success fill:#10b981,stroke:#047857,color:#ffffff",
  "classDef error fill:#ef4444,stroke:#b91c1c,color:#ffffff",
].join("\n");

let diagramCounter = 0;

interface OrchestrationGraphProps {
  /** 一个 OrchestrationMode.name —— 用于获取该模式静态的 graph_mermaid()。 */
  mode: string;
  /** 当前正在执行中的执行器 id（虚线显示，由后端发出）。 */
  activeNodeIds?: string[];
  /** 已完成执行器的 id。 */
  doneNodeIds?: string[];
  /** 出错执行器的 id。 */
  errorNodeIds?: string[];
}

/**
 * 某个模式的 node_id.replace(/-/g, "_") 就等于 Mermaid 图中的节点 id
 * ——这是后端刻意约定的系统性规则（见
 * orchestrator/modes/workflow_mode.py::PrePurchaseMode.graph_mermaid()
 * 中的注释），目的正是让客户端能够把实时 SSE 的 `node` 事件与图中的
 * 节点对应起来，而无需维护一份按模式硬编码的别名表。
 */
function toMermaidId(nodeId: string): string {
  return nodeId.replace(/-/g, "_");
}

/** 源码中任一侧出现在 `-->` 边上的所有节点 id。 */
function extractNodeIds(source: string): string[] {
  const ids = new Set<string>();
  const edgeRe = /(\w+)(?:\[[^\]]*\])?\s*-->\s*(\w+)(?:\[[^\]]*\])?/g;
  let match: RegExpExecArray | null;
  while ((match = edgeRe.exec(source))) {
    ids.add(match[1]);
    ids.add(match[2]);
  }
  return Array.from(ids);
}

function resolveClass(id: string, active: Set<string>, done: Set<string>, error: Set<string>): string {
  if (error.has(id)) return "error";
  if (active.has(id)) return "active";
  if (done.has(id)) return "success";
  return "core";
}

/**
 * 把某个模式的固定图以 Mermaid 实时动画的形式渲染在对话流旁边。对于没有
 * 固定拓扑的模式（`tool`、`handoff`——按轮次路由而非图），或该模式的图尚
 * 未加载完成时，不渲染任何内容。
 *
 * 渲染出的 SVG 保存在 React state 中并通过 `dangerouslySetInnerHTML` 绘制，
 * 而不是经由 ref 写入——实测确认：直接修改 ref 容器的 `innerHTML` 会被后续
 * 的 React 重渲染悄悄丢弃（React 并不知道这次修改发生过，因为该节点的 JSX
 * 本身从未变化，而随后的提交仍会把它重置）。让 SVG 走 state 这条路，React
 * 才是屏幕上内容的唯一真实归属者。
 */
export function OrchestrationGraph({
  mode,
  activeNodeIds = [],
  doneNodeIds = [],
  errorNodeIds = [],
}: OrchestrationGraphProps) {
  const [source, setSource] = useState<string | null>(null);
  const [renderedSvg, setRenderedSvg] = useState<string | null>(null);
  const diagramIdRef = useRef(`orchestration-graph-${diagramCounter++}`);

  useEffect(() => {
    let cancelled = false;
    api
      .getModeGraph(mode)
      .then((res) => {
        if (!cancelled) setSource(res.mermaid);
      })
      .catch(() => {
        if (!cancelled) setSource(null);
      });
    return () => {
      cancelled = true;
    };
  }, [mode]);

  useEffect(() => {
    if (!source) return;
    let cancelled = false;

    const active = new Set(activeNodeIds.map(toMermaidId));
    const done = new Set(doneNodeIds.map(toMermaidId));
    const error = new Set(errorNodeIds.map(toMermaidId));
    const classLines = extractNodeIds(source)
      .map((id) => `  class ${id} ${resolveClass(id, active, done, error)}`)
      .join("\n");

    const fullSource = [MERMAID_INIT, source, CLASS_DEFS, classLines].join("\n");

    import("mermaid").then(async ({ default: mermaid }) => {
      if (cancelled) return;
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
      try {
        const { svg } = await mermaid.render(diagramIdRef.current, fullSource);
        if (!cancelled) setRenderedSvg(svg);
      } catch {
        // 源码格式错误本不该出现——它来自后端自己生成的图——因此保留
        // 上一次渲染的结果，而不是让它周围的消息一起崩溃。
      }
    });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, activeNodeIds.join(","), doneNodeIds.join(","), errorNodeIds.join(",")]);

  if (!renderedSvg) return null;

  return (
    <div className="mt-2 max-w-xl overflow-x-auto rounded-lg border bg-card/60 p-3">
      <div dangerouslySetInnerHTML={{ __html: renderedSvg }} />
    </div>
  );
}
