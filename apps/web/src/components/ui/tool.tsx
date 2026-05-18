"use client"

import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { cn } from "@/lib/cn"
import {
  CheckCircle,
  ChevronDown,
  Loader2,
  NotebookPen,
  Settings,
  XCircle,
} from "lucide-react"
import { useState } from "react"

export type ToolPart = {
  type: string
  state:
    | "input-streaming"
    | "input-available"
    | "output-available"
    | "output-error"
  input?: Record<string, unknown>
  output?: Record<string, unknown>
  toolCallId?: string
  errorText?: string
}

export type ToolProps = {
  toolPart: ToolPart
  defaultOpen?: boolean
  className?: string
}

const Tool = ({ toolPart, defaultOpen = false, className }: ToolProps) => {
  const [isOpen, setIsOpen] = useState(defaultOpen)

  const { state, input, output, toolCallId } = toolPart

  const getStateIcon = () => {
    switch (state) {
      case "input-streaming":
        return <Loader2 className="h-4 w-4 animate-spin text-lens-blue" />
      case "input-available":
        return <NotebookPen className="h-4 w-4 text-grammar-violet" />
      case "output-available":
        return <CheckCircle className="h-4 w-4 text-structure-green" />
      case "output-error":
        return <XCircle className="h-4 w-4 text-error-red" />
      default:
        return <Settings className="text-muted-foreground h-4 w-4" />
    }
  }

  const getStateBadge = () => {
    const baseClasses = "rounded-pill border px-2 py-1 text-[11px] font-semibold"
    switch (state) {
      case "input-streaming":
        return (
          <span
            className={cn(
              baseClasses,
              "border-[rgba(37,99,235,0.18)] bg-lens-blue-soft text-lens-blue"
            )}
          >
            处理中
          </span>
        )
      case "input-available":
        return (
          <span
            className={cn(
              baseClasses,
              "border-[rgba(116,102,148,0.16)] bg-[rgba(116,102,148,0.08)] text-grammar-violet"
            )}
          >
            已准备
          </span>
        )
      case "output-available":
        return (
          <span
            className={cn(
              baseClasses,
              "border-[rgba(60,140,104,0.16)] bg-[rgba(60,140,104,0.08)] text-structure-green"
            )}
          >
            已完成
          </span>
        )
      case "output-error":
        return (
          <span
            className={cn(
              baseClasses,
              "border-[rgba(190,18,60,0.16)] bg-[rgba(190,18,60,0.08)] text-error-red"
            )}
          >
            出错
          </span>
        )
      default:
        return (
          <span
            className={cn(
              baseClasses,
              "border-hairline bg-reader-paper text-muted"
            )}
          >
            待处理
          </span>
        )
    }
  }

  const formatValue = (value: unknown): string => {
    if (value === null) return "null"
    if (value === undefined) return "undefined"
    if (typeof value === "string") return value
    if (typeof value === "object") {
      return JSON.stringify(value, null, 2)
    }
    return String(value)
  }

  return (
    <div
      className={cn(
        "mt-3 overflow-hidden rounded-[var(--cl-radius-surface-sm)] border border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(250,247,239,0.94))]",
        className
      )}
    >
      <Collapsible open={isOpen} onOpenChange={setIsOpen}>
        <CollapsibleTrigger asChild>
          <Button
            variant="ghost"
            className="h-auto w-full justify-between rounded-none border-0 bg-transparent px-3.5 py-3 font-normal shadow-none hover:bg-reader-paper/80"
          >
            <div className="flex items-center gap-2">
              {getStateIcon()}
              <span className="text-sm font-semibold text-ink-soft">
                {toolPart.type}
              </span>
              {getStateBadge()}
            </div>
            <ChevronDown className={cn("h-4 w-4", isOpen && "rotate-180")} />
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent
          className={cn(
            "border-t border-hairline/80",
            "data-[state=closed]:animate-collapsible-up data-[state=open]:animate-collapsible-down overflow-hidden"
          )}
        >
          <div className="space-y-3 bg-reader-paper/70 p-3.5">
            {input && Object.keys(input).length > 0 && (
              <div>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-muted">
                  输入
                </h4>
                <div className="rounded-[var(--cl-radius-control-md)] border border-hairline bg-surface px-3 py-2 text-sm leading-6 text-ink-soft">
                  {Object.entries(input).map(([key, value]) => (
                    <div key={key} className="mb-1">
                      <span className="text-muted">{key}:</span>{" "}
                      <span>{formatValue(value)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {output && (
              <div>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-muted">
                  输出
                </h4>
                <div className="max-h-60 overflow-auto rounded-[var(--cl-radius-control-md)] border border-hairline bg-surface px-3 py-2 text-sm leading-6 text-ink-soft">
                  <pre className="whitespace-pre-wrap">
                    {formatValue(output)}
                  </pre>
                </div>
              </div>
            )}

            {state === "output-error" && toolPart.errorText && (
              <div>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-error-red">错误</h4>
                <div className="rounded-[var(--cl-radius-control-md)] border border-[rgba(190,18,60,0.14)] bg-[rgba(190,18,60,0.05)] px-3 py-2 text-sm text-error-red">
                  {toolPart.errorText}
                </div>
              </div>
            )}

            {state === "input-streaming" && (
              <div className="text-sm text-muted">
                正在整理这一步的结果。
              </div>
            )}

            {toolCallId && (
              <div className="border-t border-hairline/80 pt-2 text-xs text-subtle">
                <span>步骤 ID：{toolCallId}</span>
              </div>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}

export { Tool }
