"use client";

import { cn } from "@/lib/cn";
import { CheckCircle, ChevronDown, Loader2, Settings, XCircle } from "lucide-react";
import { useState } from "react";

export type ToolPart = {
  type: string;
  state:
    | "input-streaming"
    | "input-available"
    | "output-available"
    | "output-error";
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  toolCallId?: string;
  errorText?: string;
};

export type ToolProps = {
  toolPart: ToolPart;
  defaultOpen?: boolean;
  className?: string;
};

const Tool = ({ toolPart, defaultOpen = false, className }: ToolProps) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  const { state, input, output, toolCallId } = toolPart;

  const getStateIcon = () => {
    switch (state) {
      case "input-streaming":
        return <Loader2 className="h-4 w-4 animate-spin text-lens-blue" />;
      case "input-available":
        return <Settings className="h-4 w-4 text-vocab-amber" />;
      case "output-available":
        return <CheckCircle className="h-4 w-4 text-structure-green" />;
      case "output-error":
        return <XCircle className="h-4 w-4 text-destructive" />;
      default:
        return <Settings className="h-4 w-4 text-muted-foreground" />;
    }
  };

  const getStateBadge = () => {
    const baseClasses = "rounded-full px-2 py-1 text-[11px] font-medium";
    switch (state) {
      case "input-streaming":
        return <span className={cn(baseClasses, "bg-lens-blue-soft text-lens-blue")}>进行中</span>;
      case "input-available":
        return <span className={cn(baseClasses, "bg-reader-paper text-muted")}>准备中</span>;
      case "output-available":
        return <span className={cn(baseClasses, "bg-[#DCEEE2] text-[#356048]")}>已完成</span>;
      case "output-error":
        return <span className={cn(baseClasses, "bg-[#F7DFDE] text-[#9B3F3B]")}>失败</span>;
      default:
        return <span className={cn(baseClasses, "bg-reader-paper text-muted")}>等待中</span>;
    }
  };

  const formatValue = (value: unknown): string => {
    if (value === null) return "null";
    if (value === undefined) return "undefined";
    if (typeof value === "string") return value;
    if (typeof value === "object") {
      return JSON.stringify(value, null, 2);
    }
    return String(value);
  };

  return (
    <div className={cn("mt-3 overflow-hidden rounded-lg border border-hairline", className)}>
      <button
        type="button"
        className="flex h-auto w-full items-center justify-between bg-background px-3 py-2 text-left"
        onClick={() => setIsOpen((value) => !value)}
      >
        <div className="flex items-center gap-2">
          {getStateIcon()}
          <span className="font-mono text-sm font-medium">{toolPart.type}</span>
          {getStateBadge()}
        </div>
        <ChevronDown className={cn("h-4 w-4 text-muted", isOpen && "rotate-180")} />
      </button>
      {isOpen ? (
        <div className="border-t border-hairline bg-background p-3">
          {input && Object.keys(input).length > 0 ? (
            <div className="mb-3">
              <h4 className="mb-2 text-sm font-medium text-muted">Input</h4>
              <div className="rounded border border-hairline bg-background p-2 font-mono text-sm">
                {Object.entries(input).map(([key, value]) => (
                  <div key={key} className="mb-1">
                    <span className="text-muted">{key}:</span> <span>{formatValue(value)}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {output ? (
            <div className="mb-3">
              <h4 className="mb-2 text-sm font-medium text-muted">Output</h4>
              <div className="max-h-60 overflow-auto rounded border border-hairline bg-background p-2 font-mono text-sm">
                <pre className="whitespace-pre-wrap">{formatValue(output)}</pre>
              </div>
            </div>
          ) : null}

          {state === "output-error" && toolPart.errorText ? (
            <div className="mb-3">
              <h4 className="mb-2 text-sm font-medium text-destructive">Error</h4>
              <div className="rounded border border-destructive/20 bg-destructive/10 p-2 text-sm text-destructive">
                {toolPart.errorText}
              </div>
            </div>
          ) : null}

          {state === "input-streaming" ? (
            <div className="text-sm text-muted">正在处理中…</div>
          ) : null}

          {toolCallId ? (
            <div className="border-t border-hairline pt-2 text-xs text-muted">
              <span className="font-mono">Call ID: {toolCallId}</span>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};

export { Tool };
