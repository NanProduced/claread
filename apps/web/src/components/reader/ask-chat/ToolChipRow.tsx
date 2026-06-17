"use client";

import React from "react";
import type {
  ReaderAskToolTraceEntryDto,
  ReaderAskToolStatusDto,
} from "@/types/api/reader-ask";
import { cn } from "@/lib/cn";

const statusDotColor: Record<ReaderAskToolStatusDto, string> = {
  completed: "bg-green-500",
  started: "bg-yellow-500",
  failed: "bg-red-500",
};

function defaultToolLabel(toolName: string): string {
  switch (toolName) {
    case "get_record_context":
      return "上下文";
    case "get_record_insights":
      return "解析";
    case "get_user_vocabulary_book":
      return "生词本";
    case "resolve_known_reference":
      return "引用";
    case "generate_sentence_annotation":
      return "标注";
    case "propose_save_note":
      return "笔记";
    case "propose_save_highlight":
      return "高亮";
    case "suggest_prompts":
      return "追问";
    default:
      return toolName;
  }
}

type ToolChipRowProps = {
  entries: ReaderAskToolTraceEntryDto[];
  toolLabelFn?: (toolName: string) => string;
  className?: string;
};

const MAX_VISIBLE = 5;

export function ToolChipRow({
  entries,
  toolLabelFn,
  className,
}: ToolChipRowProps) {
  if (entries.length === 0) return null;

  const labelFn = toolLabelFn ?? defaultToolLabel;
  const visible = entries.slice(0, MAX_VISIBLE);
  const overflow = entries.length - MAX_VISIBLE;

  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {visible.map((entry, i) => (
        <span
          key={`${entry.tool_name}-${i}`}
          className="inline-flex items-center gap-1 rounded-full border border-border/50 bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground"
        >
          <span
            className={cn("inline-block size-1.5 shrink-0 rounded-full", statusDotColor[entry.status])}
          />
          {labelFn(entry.tool_name)}
        </span>
      ))}
      {overflow > 0 && (
        <span className="inline-flex items-center rounded-full border border-border/50 bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground">
          +{overflow}
        </span>
      )}
    </div>
  );
}
