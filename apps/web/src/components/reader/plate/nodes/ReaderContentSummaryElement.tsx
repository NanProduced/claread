"use client";

import { ChevronDown, MessageSquare } from "lucide-react";
import { useState } from "react";
import type { RenderElement } from "platejs/react";
import type { ReaderContentSummaryNode } from "@/lib/reader-plate";
import { contentSummaryCompletenessLabel } from "../shared";

interface ReaderContentSummaryElementProps {
  props: Parameters<RenderElement>[0];
  onAsk?: () => void;
  routeFocused?: boolean;
}

export function ReaderContentSummaryElement({
  onAsk,
  props,
  routeFocused = false,
}: ReaderContentSummaryElementProps) {
  const [expanded, setExpanded] = useState(false);
  const element = props.element as unknown as ReaderContentSummaryNode;

  return (
    <section
      {...props.attributes}
      id="reader-content-summary"
      className={`group/content-summary mx-auto mb-8 max-w-[72ch] rounded-xl border border-border/80 bg-popover/98 px-4 py-4 shadow-sm sm:px-5 ${routeFocused ? "reader-route-focus-frame" : ""}`.trim()}
      data-reader-anchor="content_summary"
      data-reader-node="content-summary"
      data-expanded={expanded ? "true" : "false"}
    >
      <div className="flex items-start gap-3">
        <button
          type="button"
          className="min-w-0 flex-1 text-left"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
          aria-label={`${expanded ? "收起" : "展开"}内容概要`}
        >
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[0.72rem] font-semibold uppercase tracking-[0.12em] text-lens-blue">内容概要</p>
            <span className="rounded-full border border-border bg-background px-2.5 py-0.5 text-[0.68rem] font-medium text-muted-foreground">
              {contentSummaryCompletenessLabel(element.completeness)}
            </span>
          </div>
          <div className="mt-1 flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <h2 className="text-[1rem] font-semibold leading-6 text-foreground">学术内容摘要</h2>
              {!expanded ? (
                <p className="mt-1 line-clamp-2 text-[0.9rem] leading-6 text-muted-foreground">{element.overview}</p>
              ) : null}
            </div>
            <span className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border bg-background text-muted-foreground">
              <ChevronDown
                aria-hidden="true"
                className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-180" : ""}`}
              />
            </span>
          </div>
        </button>

        {onAsk ? (
          <button
            type="button"
            className="focus-ring inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-transparent bg-transparent text-muted opacity-0 transition-[opacity,border-color,color,background-color] hover:border-border hover:bg-muted/50 hover:text-lens-blue focus-visible:opacity-100 group-hover/content-summary:opacity-100"
            onClick={(event) => {
              event.stopPropagation();
              onAsk();
            }}
            aria-label="带内容概要进入 Ask"
          >
            <MessageSquare aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>

      {expanded ? (
        <div className="mt-3 border-t border-border/80 pt-3 space-y-4 text-sm leading-7 text-muted-foreground">
          <p className="text-[0.97rem] leading-[1.85] text-foreground">{element.overview}</p>
          {element.researchQuestion ? (
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">研究问题</p>
              <p>{element.researchQuestion}</p>
            </div>
          ) : null}
          {element.methodology ? (
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">方法</p>
              <p>{element.methodology}</p>
            </div>
          ) : null}
          {element.keyFindings.length > 0 ? (
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">主要发现</p>
              <ul className="list-disc space-y-1 pl-5">
                {element.keyFindings.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {element.limitations.length > 0 ? (
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">局限性</p>
              <ul className="list-disc space-y-1 pl-5">
                {element.limitations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}

      <span className="hidden">{props.children}</span>
    </section>
  );
}
