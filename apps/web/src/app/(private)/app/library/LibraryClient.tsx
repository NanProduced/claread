"use client";

import { Plus, Search } from "lucide-react";
import Link from "next/link";
import type { Route } from "next";
import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, useTransition } from "react";
import {
  usePathname,
  useRouter,
  useSearchParams,
  type ReadonlyURLSearchParams,
} from "next/navigation";
import { Button } from "@/components/primitives/button";
import { appReadRoute } from "@/lib/routes";
import type {
  ReadingRecordListItemVm,
  ReadingRecordsBffError,
} from "@/services/bff/reading-records";
import { ReadingRecordSection } from "./ReadingRecordSection";
import * as ScrollAreaPrimitive from "@radix-ui/react-scroll-area";
import { ScrollBar } from "@/components/primitives/scroll-area";

const libraryScrollStoragePrefix = "claread.library.scroll.";

function normalizeLibraryQuery(value: string) {
  return value.trim().toLowerCase();
}

function queryFromParams(searchParams: URLSearchParams | ReadonlyURLSearchParams) {
  return searchParams.get("q") ?? "";
}

export function LibraryClient({
  readingRecords,
  readingRecordsStatus,
  readingRecordsMessage,
}: {
  readingRecords: ReadingRecordListItemVm[];
  readingRecordsStatus: "ready" | ReadingRecordsBffError["code"];
  readingRecordsMessage?: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [, startTransition] = useTransition();
  const scrollViewportRef = useRef<HTMLDivElement | null>(null);
  const [query, setQuery] = useState(() => queryFromParams(searchParams));
  const deferredQuery = useDeferredValue(query);
  const [records, setRecords] = useState(readingRecords);

  // Sync local records when the server component refreshes its props
  // (e.g. after a delete elsewhere triggers router.refresh()).  Adjusted
  // during render (React's canonical state-during-render pattern) so the
  // server refresh never fights a local optimistic removal.
  const [prevRecords, setPrevRecords] = useState(readingRecords);
  if (readingRecords !== prevRecords) {
    setPrevRecords(readingRecords);
    setRecords(readingRecords);
  }

  const normalizedQuery = normalizeLibraryQuery(deferredQuery);
  const currentSearch = searchParams.toString();
  const scrollStorageKey = `${libraryScrollStoragePrefix}${pathname}?${currentSearch}`;

  const rememberLibraryScrollPosition = useCallback(() => {
    if (typeof window === "undefined") {
      return;
    }

    const viewport = scrollViewportRef.current;
    if (!viewport) {
      return;
    }

    window.sessionStorage.setItem(scrollStorageKey, String(viewport.scrollTop));
  }, [scrollStorageKey]);

  // Sync URL search param to query state (adjust state during render).
  const [prevSearchParamsKey, setPrevSearchParamsKey] = useState(currentSearch);
  if (currentSearch !== prevSearchParamsKey) {
    setPrevSearchParamsKey(currentSearch);
    const nextQuery = queryFromParams(searchParams);
    setQuery((current) => (current === nextQuery ? current : nextQuery));
  }

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const viewport = scrollViewportRef.current;
    if (!viewport) {
      return;
    }

    const savedTop = Number(window.sessionStorage.getItem(scrollStorageKey) ?? "0");
    const nextTop = Number.isFinite(savedTop) ? savedTop : 0;
    const frame = window.requestAnimationFrame(() => {
      viewport.scrollTo({ top: nextTop });
    });

    return () => window.cancelAnimationFrame(frame);
  }, [scrollStorageKey]);

  useEffect(() => {
    return () => {
      rememberLibraryScrollPosition();
    };
  }, [rememberLibraryScrollPosition]);

  useEffect(() => {
    const params = new URLSearchParams(searchParams.toString());
    const trimmedQuery = query.trim();

    if (trimmedQuery) {
      params.set("q", trimmedQuery);
    } else {
      params.delete("q");
    }

    const nextSearch = params.toString();
    const currentSearchValue = searchParams.toString();

    if (nextSearch === currentSearchValue) {
      return;
    }

    startTransition(() => {
      router.replace((nextSearch ? `${pathname}?${nextSearch}` : pathname) as Route, { scroll: false });
    });
  }, [pathname, query, router, searchParams]);

  const filteredReadingRecords = useMemo(() => {
    if (!normalizedQuery) {
      return records;
    }

    return records.filter((record) =>
      record.title.toLowerCase().includes(normalizedQuery),
    );
  }, [normalizedQuery, records]);

  const handleRecordDeleted = useCallback((recordId: string) => {
    setRecords((current) => current.filter((record) => record.readingRecordId !== recordId));
  }, []);

  const hasQuery = normalizedQuery.length > 0;

  const resultCountLabel = hasQuery
    ? `找到 ${filteredReadingRecords.length} 篇记录`
    : `共 ${filteredReadingRecords.length} 篇记录`;

  function resetQuery() {
    setQuery("");
  }

  return (
    <div className="flex h-full min-h-0 flex-col lg:py-12">
      {/* Library Header */}
      <div className="mb-6 shrink-0 flex flex-col sm:flex-row sm:items-end justify-between gap-4 border-b border-hairline pb-5">
        <div>
          <div className="mb-2 flex items-center gap-3">
            <p className="text-[0.6rem] font-bold tracking-[0.2em] text-lens-blue">Library</p>
            <div className="h-[1px] w-8 bg-hairline" />
          </div>
          <h1 className="font-headline text-[2rem] font-semibold leading-[1] tracking-tight text-ink md:text-[2.5rem] lg:text-[3rem]">
            阅读记录
          </h1>
        </div>
        <div className="pb-1">
          <Button asChild variant="primary-ink" className="group px-6 py-3 font-sans text-[0.82rem] font-semibold tracking-[0.08em] transition-all duration-300 border-transparent min-w-[130px]">
            <Link href={appReadRoute} className="flex items-center justify-center">
              <Plus aria-hidden="true" className="mr-2 h-4 w-4 transition-transform duration-300 group-hover:rotate-90" />
              新解读
            </Link>
          </Button>
        </div>
      </div>

      {/* Search + count */}
      <div className="mb-4 shrink-0 flex items-center justify-between">
        <div className="flex w-full max-w-sm items-center gap-3">
          <Search className="h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            aria-label="搜索阅读记录标题"
            placeholder="搜索阅读记录标题..."
            className="w-full bg-transparent text-[0.95rem] text-ink outline-none placeholder:text-muted-foreground"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <p className="text-[0.72rem] font-semibold tracking-[0.08em] text-ink">
          {resultCountLabel}
        </p>
      </div>

      {/* Reading Record list body */}
      <ScrollAreaPrimitive.Root className="min-h-0 flex-1 relative overflow-hidden">
        <ScrollAreaPrimitive.Viewport
          ref={scrollViewportRef}
          onScroll={rememberLibraryScrollPosition}
          className="h-full w-full rounded-[inherit]"
        >
          <ReadingRecordSection
            readingRecords={filteredReadingRecords}
            status={readingRecordsStatus}
            message={readingRecordsMessage}
            hasQuery={hasQuery}
            onResetQuery={hasQuery ? resetQuery : undefined}
            onRecordDeleted={handleRecordDeleted}
          />
        </ScrollAreaPrimitive.Viewport>
        <ScrollBar />
      </ScrollAreaPrimitive.Root>
    </div>
  );
}
