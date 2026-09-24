"use client";

import { useEffect, useState } from "react";
import { api, type CompareModeResult, type OrchestrationMode } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { OrchestrationGraph } from "@/components/chat/orchestration-graph";
import { Scale, Loader2 } from "lucide-react";

interface ModeComparisonProps {
  /** 预填对比用的提示词——通常是输入框里已有的内容。 */
  initialPrompt?: string;
}

/**
 * 差异化亮点（计划 Phase 1.6c）：把同一条提示词并排跑过多种编排模式
 * ——工具路由 vs. 处理权交接 vs. 工作流图——直观看到延迟/步数/回答上
 * 的真实差异，而不是只读到相关描述。它独立于对话会话（POST
 * /api/orchestration/compare 不持久化任何内容，也不触及历史记录）。
 */
export function ModeComparison({ initialPrompt = "" }: ModeComparisonProps) {
  const [open, setOpen] = useState(false);
  const [modes, setModes] = useState<OrchestrationMode[]>([]);
  const [prompt, setPrompt] = useState(initialPrompt);
  const [selected, setSelected] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<CompareModeResult[] | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    api
      .getOrchestrationModes()
      .then((data) => {
        setModes(data);
        setSelected((prev) => (prev.length > 0 ? prev : data.filter((m) => m.default).map((m) => m.name)));
      })
      .catch(() => setModes([]));
  }, [open]);

  useEffect(() => {
    if (open) setPrompt(initialPrompt);
  }, [open, initialPrompt]);

  function toggleMode(name: string) {
    setSelected((prev) => (prev.includes(name) ? prev.filter((m) => m !== name) : [...prev, name]));
  }

  async function runComparison() {
    if (!prompt.trim() || selected.length < 2) return;
    setRunning(true);
    setRunError(null);
    setResults(null);
    try {
      const res = await api.compareModes(prompt.trim(), selected);
      setResults(res.results);
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "对比失败。");
    } finally {
      setRunning(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button variant="outline" size="sm" title="在同一条提示词上对比各编排模式">
            <Scale className="mr-1.5 size-3.5" />
            对比
          </Button>
        }
      />
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>对比编排模式</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 pt-2">
          <Textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="例如：这副耳机值得买吗？"
            rows={2}
          />

          <div className="flex flex-wrap gap-1.5">
            {modes.map((mode) => (
              <Badge
                key={mode.name}
                variant={selected.includes(mode.name) ? "default" : "outline"}
                className="cursor-pointer select-none"
                onClick={() => toggleMode(mode.name)}
              >
                {mode.label}
              </Badge>
            ))}
          </div>

          <Button onClick={runComparison} disabled={running || !prompt.trim() || selected.length < 2}>
            {running && <Loader2 className="mr-2 size-4 animate-spin" />}
            开始对比{selected.length < 2 ? "（至少选择 2 种模式）" : ""}
          </Button>

          {runError && <p className="text-sm text-destructive">{runError}</p>}

          {results && (
            <div className="grid gap-3 sm:grid-cols-2">
              {results.map((result) => (
                <Card key={result.mode} className={result.error ? "border-destructive/40" : undefined}>
                  <CardHeader>
                    <CardTitle className="flex items-center justify-between gap-2 text-sm">
                      <span>{result.label}</span>
                      <Badge variant="secondary" className="text-[10px] font-normal">
                        {result.latency_ms}ms
                      </Badge>
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    {result.error ? (
                      <p className="text-destructive">{result.error}</p>
                    ) : (
                      <>
                        <p className="whitespace-pre-wrap text-foreground/90">{result.text || "（无回答）"}</p>
                        <div className="flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
                          <span>{result.step_count} 步</span>
                          {result.agents_involved.map((agent) => (
                            <Badge key={agent} variant="outline" className="text-[10px] font-normal">
                              {agent}
                            </Badge>
                          ))}
                        </div>
                        {result.graph_mermaid && (
                          <OrchestrationGraph mode={result.mode} doneNodeIds={result.agents_involved} />
                        )}
                      </>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
