'use client';

import { useEffect, useRef } from 'react';
import { useRecentReading } from '@/components/layout/recent-reading-context';

export type SnapshotStateKind = 'idle' | 'loading' | 'loaded' | 'error';

export function ReaderOpenedBeacon({
  recordId,
  snapshotStateKind,
}: {
  recordId: string;
  snapshotStateKind: SnapshotStateKind;
}) {
  const { refetch } = useRecentReading();
  const lastFiredRecordIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (snapshotStateKind !== 'loaded') return;
    if (lastFiredRecordIdRef.current === recordId) return;
    lastFiredRecordIdRef.current = recordId;
    void fetch(`/api/web/reader/records/${recordId}/opened`, { method: 'POST' })
      .then((res) => { if (res.ok) { void refetch(); } })
      .catch(() => {/* network error: silent, no refetch */});
  }, [snapshotStateKind, recordId, refetch]);

  return null;
}