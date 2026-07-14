'use client';

import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import type { ReadingRecordListItemVm } from '@/services/bff/reading-records';

interface RecentReadingContextValue {
  items: ReadingRecordListItemVm[];
  refetch: () => Promise<void>;
}

const RecentReadingContext = createContext<RecentReadingContextValue | null>(null);

export function RecentReadingProvider({
  initialItems,
  children,
}: {
  initialItems: ReadingRecordListItemVm[];
  children: ReactNode;
}) {
  const [items, setItems] = useState<ReadingRecordListItemVm[]>(initialItems);

  const refetch = useCallback(async () => {
    try {
      const res = await fetch('/api/web/reading-records?limit=10', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.ok === true && Array.isArray(data.items)) {
        setItems(data.items);
      }
    } catch {
      // best-effort
    }
  }, []);

  return (
    <RecentReadingContext.Provider value={{ items, refetch }}>
      {children}
    </RecentReadingContext.Provider>
  );
}

const noopRefetch = async (): Promise<void> => {
  // best-effort no-op when no provider is mounted (e.g. in tests or isolated
  // components). Components should treat the empty list as "no recent reading
  // available" rather than crashing the render tree.
};

const fallbackContext: RecentReadingContextValue = {
  items: [],
  refetch: noopRefetch,
};

export function useRecentReading(): RecentReadingContextValue {
  const ctx = useContext(RecentReadingContext);
  if (!ctx) {
    return fallbackContext;
  }
  return ctx;
}
