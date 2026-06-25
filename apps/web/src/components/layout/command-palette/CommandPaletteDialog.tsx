"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { Route } from "next";
import {
  CommandMenuDialog,
  CommandMenuEmpty,
  CommandMenuGroup,
  CommandMenuInput,
  CommandMenuItem,
  CommandMenuList,
  CommandMenuSeparator,
  CommandMenuShortcut,
} from "@/components/primitives/command-menu";
import { Kbd } from "@/components/primitives/kbd";
import { formatShortcut } from "@/lib/shortcuts";
import type {
  ReadingRecordListItemVm,
  ReadingRecordListResult,
} from "@/services/bff/reading-records";
import { getCommandCommands, getPageCommands } from "./command-palette-items";
import type { CommandPaletteCommand } from "./command-palette-types";
import { useCommandPalette } from "./useCommandPalette";

function fetchReadingRecords(
  query?: string,
  signal?: AbortSignal,
): Promise<ReadingRecordListItemVm[]> {
  const params = new URLSearchParams();
  params.set("limit", "8");

  if (query?.trim()) {
    params.set("query", query.trim());
  }

  return fetch(`/api/web/reading-records?${params.toString()}`, { signal })
    .then((res) => res.json())
    .then((data: ReadingRecordListResult) => (data.ok ? data.items : []))
    .catch((err: unknown) => {
      if (err instanceof DOMException && err.name === "AbortError") {
        return [];
      }
      return [] as ReadingRecordListItemVm[];
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

export function CommandPaletteDialog() {
  const router = useRouter();
  const open = useCommandPalette((s) => s.open);
  const setOpen = useCommandPalette((s) => s.setOpen);
  const [query, setQuery] = useState("");
  const [recentRecords, setRecentRecords] = useState<ReadingRecordListItemVm[]>(
    [],
  );
  const [searchedRecords, setSearchedRecords] = useState<
    ReadingRecordListItemVm[]
  >([]);
  const [loading, setLoading] = useState(false);
  const [recordsLoaded, setRecordsLoaded] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const navigate = useCallback(
    (href: string) => {
      setOpen(false);
      router.push(href as Route);
    },
    [router, setOpen],
  );

  const lastReaderUrl = recentRecords[0]?.readerUrl;
  const pageCommands = getPageCommands(navigate);
  const commandCommands = getCommandCommands(navigate, lastReaderUrl);

  useEffect(() => {
    if (open && !recordsLoaded && query.trim().length === 0) {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      queueMicrotask(() => setLoading(true));

      fetchReadingRecords(undefined, controller.signal)
        .then((items) => {
          if (!controller.signal.aborted) {
            setRecentRecords(items);
            setRecordsLoaded(true);
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) {
            setLoading(false);
          }
        });
    }
  }, [open, query, recordsLoaded]);

  useEffect(() => {
    if (!open) {
      abortRef.current?.abort();
      abortRef.current = null;
      queueMicrotask(() => {
        setLoading(false);
        setQuery("");
        setSearchedRecords([]);
      });
    }
  }, [open]);

  useEffect(() => {
    if (!query.trim()) {
      queueMicrotask(() => setSearchedRecords([]));
      return;
    }

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);

      fetchReadingRecords(query, controller.signal)
        .then((items) => {
          if (!controller.signal.aborted) {
            setSearchedRecords(items);
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) {
            setLoading(false);
          }
        });
    }, 250);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      abortRef.current?.abort();
    };
  }, [query]);

  const hasQuery = query.trim().length > 0;
  const visibleRecords = hasQuery ? searchedRecords : recentRecords;
  const recordCommands: CommandPaletteCommand[] = visibleRecords.map((record) => ({
    id: `record-${record.readingRecordId}`,
    label: record.title,
    group: hasQuery ? "search" : "recent",
    onSelect: () => {
      setOpen(false);
      router.push(record.readerUrl as Route);
    },
  }));
  const recordGroupHeading = hasQuery ? "搜索阅读记录" : "最近阅读记录";
  const hasRecords = recordCommands.length > 0;
  const showRecordGroup = !hasQuery || loading || searchedRecords.length > 0;

  return (
    <CommandMenuDialog open={open} onOpenChange={setOpen}>
      <CommandMenuInput
        placeholder="搜索或跳转"
        value={query}
        onValueChange={setQuery}
      />
      <CommandMenuList>
        <CommandMenuEmpty>未找到匹配结果</CommandMenuEmpty>

        <CommandMenuGroup heading="页面">
          {pageCommands.map((cmd) => {
            const Icon = cmd.icon;
            return (
              <CommandMenuItem key={cmd.id} onSelect={cmd.onSelect} value={cmd.label}>
                {Icon && <Icon className="h-4 w-4 text-muted" />}
                {cmd.label}
                {cmd.shortcut ? (
                  <CommandMenuShortcut>
                    {formatShortcut(cmd.shortcut)}
                  </CommandMenuShortcut>
                ) : null}
              </CommandMenuItem>
            );
          })}
        </CommandMenuGroup>

        {showRecordGroup && (
          <>
            <CommandMenuSeparator />
            <CommandMenuGroup heading={recordGroupHeading}>
              {loading && !hasRecords ? (
                <CommandMenuItem disabled>加载中...</CommandMenuItem>
              ) : (
                recordCommands.map((cmd) => {
                  const record = visibleRecords.find(
                    (item) => `record-${item.readingRecordId}` === cmd.id,
                  );

                  return (
                    <CommandMenuItem key={cmd.id} onSelect={cmd.onSelect} value={cmd.label}>
                      <div className="flex-1 truncate font-reading reader-serif text-[0.92rem] font-semibold text-ink">
                        {cmd.label}
                      </div>
                      {record ? (
                        <span className="ml-3 shrink-0 text-[0.72rem] text-muted/80">
                          {formatRelativeTime(record.createdAt)}
                        </span>
                      ) : null}
                    </CommandMenuItem>
                  );
                })
              )}
            </CommandMenuGroup>
          </>
        )}

        <CommandMenuSeparator />
        <CommandMenuGroup heading="命令">
          {commandCommands.map((cmd) => (
            <CommandMenuItem
              key={cmd.id}
              onSelect={cmd.onSelect}
              disabled={cmd.disabled}
              value={cmd.label}
            >
              {cmd.label}
              {cmd.shortcut ? (
                <CommandMenuShortcut>{formatShortcut(cmd.shortcut)}</CommandMenuShortcut>
              ) : null}
            </CommandMenuItem>
          ))}
        </CommandMenuGroup>
      </CommandMenuList>
      <div className="flex items-center gap-3.5 border-t border-hairline/60 bg-[color-mix(in_srgb,var(--surface)_36%,transparent)] px-4 py-2.5 text-[0.72rem] text-muted/80">
        <span className="flex items-center gap-1.5">
          <Kbd className="px-1 py-0.5">↑</Kbd>
          <Kbd className="px-1 py-0.5">↓</Kbd>
          <span>选择</span>
        </span>
        <span className="h-3 w-px bg-hairline/70" />
        <span className="flex items-center gap-1.5">
          <Kbd className="px-1 py-0.5">Enter</Kbd>
          <span>打开</span>
        </span>
        <span className="h-3 w-px bg-hairline/70" />
        <span className="flex items-center gap-1.5">
          <Kbd className="px-1 py-0.5">Esc</Kbd>
          <span>关闭</span>
        </span>
      </div>
    </CommandMenuDialog>
  );
}
