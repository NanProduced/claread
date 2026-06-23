"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CommandMenuGroup,
  CommandMenuItem,
  CommandMenuSeparator,
} from "@/components/primitives/command-menu";
import type { ReadingRecordListResult } from "@/services/bff/reading-records";

interface ReadingRecordCommandItem {
  readingRecordId: string;
  readerUrl: string;
  title: string;
  createdAt: string;
}

interface ReadingRecordCommandState {
  items: ReadingRecordCommandItem[];
  loading: boolean;
  loaded: boolean;
}

function fetchReadingRecords(
  signal?: AbortSignal,
): Promise<ReadingRecordCommandItem[]> {
  return fetch("/api/web/reading-records?limit=6", { signal })
    .then((res) => res.json())
    .then((result: ReadingRecordListResult) => (result.ok ? result.items : []))
    .catch((err: unknown) => {
      if (err instanceof DOMException && err.name === "AbortError") {
        return [];
      }
      return [];
    });
}

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return "今天";
  if (diffDays === 1) return "昨天";
  if (diffDays < 7) return `${diffDays} 天前`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)} 周前`;
  return `${Math.floor(diffDays / 30)} 月前`;
}

function useReadingRecordCommands(open: boolean) {
  const [state, setState] = useState<ReadingRecordCommandState>({
    items: [],
    loading: false,
    loaded: false,
  });
  const abortRef = useRef<AbortController | null>(null);
  const loadingRef = useRef(false);
  const loadedRef = useRef(false);

  useEffect(() => {
    if (!open) {
      abortRef.current?.abort();
      abortRef.current = null;
      loadingRef.current = false;
      return;
    }

    if (loadedRef.current || loadingRef.current) {
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    loadingRef.current = true;
    queueMicrotask(() => {
      if (!controller.signal.aborted) {
        setState((current) => ({ ...current, loading: true }));
      }
    });

    fetchReadingRecords(controller.signal)
      .then((items) => {
        if (!controller.signal.aborted) {
          loadedRef.current = true;
          loadingRef.current = false;
          setState({ items, loading: false, loaded: true });
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          loadingRef.current = false;
          setState({ items: [], loading: false, loaded: true });
        }
      });

    return () => {
      controller.abort();
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      loadingRef.current = false;
    };
  }, [open]);

  return state;
}

export function ReadingRecordCommandGroup({
  open,
  query,
  onOpenReadingRecord,
}: {
  open: boolean;
  query: string;
  onOpenReadingRecord: (readerUrl: string) => void;
}) {
  const { items, loading } = useReadingRecordCommands(open);
  const trimmedQuery = query.trim().toLowerCase();
  const visibleItems = useMemo(() => {
    if (!trimmedQuery) {
      return items;
    }

    return items.filter((item) =>
      item.title.toLowerCase().includes(trimmedQuery),
    );
  }, [items, trimmedQuery]);

  if (!loading && visibleItems.length === 0) {
    return null;
  }

  return (
    <>
      <CommandMenuSeparator />
      <CommandMenuGroup heading="新阅读记录">
        {loading && visibleItems.length === 0 ? (
          <CommandMenuItem disabled>加载新阅读记录中...</CommandMenuItem>
        ) : (
          visibleItems.map((item) => (
            <CommandMenuItem
              key={`reading-record-${item.readingRecordId}`}
              onSelect={() => onOpenReadingRecord(item.readerUrl)}
            >
              <div className="flex-1 truncate font-reading reader-serif text-[0.92rem] font-semibold text-ink">
                {item.title}
              </div>
              <span className="ml-3 shrink-0 text-[0.72rem] text-muted/80">
                {formatRelativeTime(item.createdAt)}
              </span>
            </CommandMenuItem>
          ))
        )}
      </CommandMenuGroup>
    </>
  );
}
