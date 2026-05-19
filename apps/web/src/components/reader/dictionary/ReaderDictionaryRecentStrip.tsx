"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { DictionaryLookupSnapshot } from "./contracts";
import { dictionaryLookupHistoryKey, dictionaryLookupHistorySummary } from "./shared";

interface ReaderDictionaryRecentStripProps {
  history: DictionaryLookupSnapshot[];
  activeLookup: DictionaryLookupSnapshot | null;
  onSelectHistory: (lookup: DictionaryLookupSnapshot) => void;
}

export function ReaderDictionaryRecentStrip({
  activeLookup,
  history,
  onSelectHistory,
}: ReaderDictionaryRecentStripProps) {
  const recentItems = history.slice(0, 8);
  const [collapsed, setCollapsed] = useState(recentItems.length > 3);

  if (recentItems.length <= 1) {
    return null;
  }

  return (
    <section className="rounded-[18px] border border-hairline/85 bg-surface/84 px-3.5 py-3.5 shadow-[0_1px_2px_rgba(17,17,17,0.04)]">
      <button
        type="button"
        className="focus-ring flex min-h-11 w-full items-center justify-between gap-3 text-left"
        onClick={() => setCollapsed((value) => !value)}
        aria-expanded={!collapsed}
      >
        <span className="text-[0.72rem] font-semibold tracking-[0.06em] text-muted">查过历史</span>
        <span className="inline-flex items-center gap-2 text-[0.68rem] font-semibold text-subtle">
          <span className="reader-dictionary-meta-tag reader-dictionary-meta-tag--count px-2 py-0.5">
            {recentItems.length}
          </span>
          {collapsed ? (
            <ChevronDown aria-hidden="true" className="h-3.5 w-3.5" />
          ) : (
            <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />
          )}
        </span>
      </button>
      {!collapsed ? (
        <div className="mt-3 max-h-[18rem] overflow-y-auto pr-1">
          <div className="space-y-1.5">
            {recentItems.map((item) => {
              const active =
                activeLookup?.query.toLowerCase() === item.query.toLowerCase() &&
                activeLookup?.sentenceId === item.sentenceId;
              const summary = dictionaryLookupHistorySummary(item);
              return (
                <button
                  key={dictionaryLookupHistoryKey(item)}
                  type="button"
                  className={`focus-ring reader-dictionary-history-row block w-full rounded-[14px] border px-3 py-2.5 text-left ${
                    active ? "reader-dictionary-history-row--active border-lens-blue/16" : "border-hairline/75"
                  }`}
                  onClick={() => onSelectHistory(item)}
                  title={`${item.query}: ${summary}`}
                >
                  <div className="grid grid-cols-[minmax(0,6.35rem)_minmax(0,1fr)] items-start gap-3 md:grid-cols-[minmax(0,6.85rem)_minmax(0,1fr)]">
                    <p
                      className={`line-clamp-2 break-words text-[0.88rem] font-semibold leading-5 ${
                        active ? "text-lens-blue" : "text-ink"
                      }`}
                    >
                      {item.query}
                    </p>
                    <p className="line-clamp-2 text-[0.78rem] leading-5 text-muted">{summary}</p>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </section>
  );
}
