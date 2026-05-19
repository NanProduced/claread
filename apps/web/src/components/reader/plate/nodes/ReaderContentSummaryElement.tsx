"use client";

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
  const element = props.element as unknown as ReaderContentSummaryNode;
  return (
    <section
      {...props.attributes}
      id="reader-content-summary"
      className={`mx-auto mb-10 max-w-[72ch] rounded-note border border-hairline bg-surface px-5 py-5 shadow-surface-quiet sm:px-6 ${routeFocused ? "reader-route-focus-frame" : ""}`.trim()}
      data-reader-anchor="content_summary"
      data-reader-node="content-summary"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-lens-blue">内容概要</p>
          <h2 className="mt-1 font-headline text-xl font-semibold text-ink">Academic Summary</h2>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full border border-hairline bg-surface-warm px-3 py-1 text-[0.72rem] font-semibold text-muted">
            {contentSummaryCompletenessLabel(element.completeness)}
          </span>
          {onAsk ? (
            <button
              type="button"
              className="focus-ring rounded-pill border border-hairline bg-surface px-3 py-1 text-[0.72rem] font-semibold text-lens-blue transition-colors hover:border-muted hover:text-ink"
              onClick={(event) => {
                event.stopPropagation();
                onAsk();
              }}
            >
              带入 Ask
            </button>
          ) : null}
        </div>
      </div>

      <div className="mt-4 space-y-4 text-sm leading-7 text-ink-soft">
        <p className="text-[0.97rem] leading-[1.85] text-ink">{element.overview}</p>
        {element.researchQuestion ? (
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">研究问题</p>
            <p>{element.researchQuestion}</p>
          </div>
        ) : null}
        {element.methodology ? (
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">方法</p>
            <p>{element.methodology}</p>
          </div>
        ) : null}
        {element.keyFindings.length > 0 ? (
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">主要发现</p>
            <ul className="list-disc space-y-1 pl-5">
              {element.keyFindings.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {element.limitations.length > 0 ? (
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">局限性</p>
            <ul className="list-disc space-y-1 pl-5">
              {element.limitations.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      <span className="hidden">{props.children}</span>
    </section>
  );
}
