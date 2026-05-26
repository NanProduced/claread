"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { Route } from "next";
import {
  CommandMenuDialog,
  CommandMenuInput,
  CommandMenuList,
  CommandMenuGroup,
  CommandMenuItem,
  CommandMenuShortcut,
  CommandMenuEmpty,
  CommandMenuSeparator,
} from "@/components/primitives/command-menu";
import { appReaderRoute } from "@/lib/routes";
import { formatShortcut } from "@/lib/shortcuts";
import { useCommandPalette } from "./useCommandPalette";
import { getPageCommands, getCommandCommands } from "./command-palette-items";
import type {
  CommandPaletteCommand,
  CommandPaletteRecordItem,
} from "./command-palette-types";
import { Kbd } from "@/components/primitives/kbd";

function fetchRecords(
  query?: string,
  signal?: AbortSignal,
): Promise<CommandPaletteRecordItem[]> {
  const params = new URLSearchParams();
  if (query) params.set("query", query);
  return fetch(`/api/web/command-palette/records?${params.toString()}`, { signal })
    .then((res) => res.json())
    .then((data: { items: CommandPaletteRecordItem[] }) => data.items)
    .catch((err: unknown) => {
      if (err instanceof DOMException && err.name === "AbortError") return [];
      return [] as CommandPaletteRecordItem[];
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
  const [recentRecords, setRecentRecords] = useState<CommandPaletteRecordItem[]>([]);
  const [searchedRecords, setSearchedRecords] = useState<CommandPaletteRecordItem[]>([]);
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

  const lastRecordId = recentRecords.length > 0 ? recentRecords[0].id : undefined;
  const pageCommands = getPageCommands(navigate);
  const commandCommands = getCommandCommands(navigate, lastRecordId);

  // Load recent records when dialog opens
  useEffect(() => {
    if (open && !recordsLoaded) {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      fetchRecords(undefined, controller.signal)
        .then((items) => {
          if (!controller.signal.aborted) {
            setRecentRecords(items);
            setRecordsLoaded(true);
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }
  }, [open, recordsLoaded]);

  // Reset state when dialog closes
  useEffect(() => {
    if (!open) {
      setQuery("");
      setSearchedRecords([]);
    }
  }, [open]);

  // Debounced search
  useEffect(() => {
    if (!query.trim()) {
      setSearchedRecords([]);
      return;
    }

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      fetchRecords(query, controller.signal).then((items) => {
        if (!controller.signal.aborted) {
          setSearchedRecords(items);
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

  const recordCommands: CommandPaletteCommand[] = (hasQuery ? searchedRecords : recentRecords).map(
    (record) => ({
      id: `record-${record.id}`,
      label: record.title,
      group: hasQuery ? "search" : "recent",
      onSelect: () => {
        setOpen(false);
        router.push(appReaderRoute(record.id));
      },
    }),
  );

  const recordGroupHeading = hasQuery ? "搜索文章" : "最近文章";
  const hasRecords = recordCommands.length > 0;
  const showRecordGroup = !hasQuery || searchedRecords.length > 0;

  return (
    <CommandMenuDialog open={open} onOpenChange={setOpen}>
      <CommandMenuInput
        placeholder="搜索或跳转"
        value={query}
        onValueChange={setQuery}
      />
      <CommandMenuList>
        <CommandMenuEmpty>未找到匹配结果</CommandMenuEmpty>

        {/* Pages group - always shown */}
        <CommandMenuGroup heading="页面">
          {pageCommands.map((cmd) => {
            const Icon = cmd.icon;
            return (
              <CommandMenuItem key={cmd.id} onSelect={cmd.onSelect}>
                {Icon && <Icon className="h-4 w-4 text-muted" />}
                {cmd.label}
                {cmd.shortcut ? (
                  <CommandMenuShortcut>{formatShortcut(cmd.shortcut)}</CommandMenuShortcut>
                ) : null}
              </CommandMenuItem>
            );
          })}
        </CommandMenuGroup>

        {/* Recent / Search records */}
        {showRecordGroup && (
          <>
            <CommandMenuSeparator />
            <CommandMenuGroup heading={recordGroupHeading}>
              {loading && !hasRecords ? (
                <CommandMenuItem disabled>加载中...</CommandMenuItem>
              ) : (
                recordCommands.map((cmd) => {
                  const record = (hasQuery ? searchedRecords : recentRecords).find(
                    (r) => `record-${r.id}` === cmd.id,
                  );
                  return (
                    <CommandMenuItem key={cmd.id} onSelect={cmd.onSelect}>
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

        {/* Commands group */}
        <CommandMenuSeparator />
        <CommandMenuGroup heading="命令">
          {commandCommands.map((cmd) => (
            <CommandMenuItem key={cmd.id} onSelect={cmd.onSelect} disabled={cmd.disabled}>
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
