"use client";

import { useEffect, useState } from "react";
import { api, type OrchestrationMode } from "@/lib/api";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";

interface ModeSwitcherProps {
  /** 一个 `OrchestrationMode.name`（例如 "tool"、"workflow:pre-purchase"），或 "" 表示使用服务端默认值。 */
  value: string;
  onChange: (mode: string) => void;
  disabled?: boolean;
}

/**
 * 输入区控件，用于选择下一轮对话由哪种编排模式执行——数据来自
 * `GET /api/orchestration/modes`。正是它让毕业项目的旗舰主张在界面上
 * 可被演示：同一个业务领域，分别通过普通 LLM 工具路由、MAF 的
 * HandoffBuilder 网状结构、或固定的工作流图来运行，且可按每条消息选择。
 *
 * 失败时优雅降级：若模式列表请求出错（或返回空列表——例如更早的、没有
 * 注册表的后端），控件不渲染任何内容，对话仍可通过服务端的默认模式正常
 * 工作。
 */
export function ModeSwitcher({ value, onChange, disabled }: ModeSwitcherProps) {
  const [modes, setModes] = useState<OrchestrationMode[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getOrchestrationModes()
      .then((data) => {
        if (!cancelled) setModes(data);
      })
      .catch(() => {
        // 模式接口不可达——对话仍可通过后端自身的默认模式工作，
        // 因此这里静默失败，而不是为一个非必需控件弹出错误提示。
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loaded && modes.length === 0) return null;

  const active = modes.find((m) => m.name === value);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        // 始终传入字符串，绝不传 undefined——实测确认：在生命周期中途于
        // 两者之间切换（首屏渲染 value="" -> undefined，选中后变成真实
        // 字符串）会让 base-ui 打出 "changing the uncontrolled value state
        // ... to be controlled" 的日志，也正是 React 对普通 input 警告的
        // 那个受控/非受控陷阱。"" 本身完全可以表示「未选择」——出问题的
        // 是类型的反复横跳，而不是空值。
        value={value}
        onValueChange={(v) => v && onChange(v)}
        disabled={disabled || modes.length === 0}
      >
        <SelectTrigger size="sm" className="w-auto min-w-40" aria-label="编排模式">
          {/* base-ui 的 SelectValue 在 `children` 为函数时会忽略 `placeholder`
              ——实测确认（空值渲染出的是空白触发器，而不是「Mode」）——
              因此空值情况必须在这里处理。 */}
          <SelectValue placeholder="编排模式">
            {(v: string) => (v ? (modes.find((m) => m.name === v)?.label ?? v) : "编排模式")}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {modes.map((mode) => (
            <SelectItem key={mode.name} value={mode.name}>
              {mode.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {active && (
        <div className="flex flex-wrap gap-1" title={active.description}>
          {active.capabilities.is_graph && (
            <Badge variant="outline" className="text-[10px] font-normal">
              图
            </Badge>
          )}
          {active.capabilities.supports_hitl && (
            <Badge variant="outline" className="text-[10px] font-normal">
              HITL
            </Badge>
          )}
          {active.capabilities.supports_checkpoints && (
            <Badge variant="outline" className="text-[10px] font-normal">
              检查点
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}
