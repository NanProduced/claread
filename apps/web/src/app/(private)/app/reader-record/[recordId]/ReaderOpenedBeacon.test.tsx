/** @vitest-environment jsdom */

import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const refetchMock = vi.fn(async () => {});
vi.mock('@/components/layout/recent-reading-context', () => ({
  useRecentReading: () => ({ items: [], refetch: refetchMock }),
}));

import { ReaderOpenedBeacon } from './ReaderOpenedBeacon';

afterEach(() => { vi.unstubAllGlobals(); });

describe('ReaderOpenedBeacon', () => {
  beforeEach(() => {
    refetchMock.mockClear();
  });

  it('POSTs opened + refetches when snapshotStateKind === "loaded"', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    render(<ReaderOpenedBeacon recordId="rr_1" snapshotStateKind="loaded" />);
    await act(async () => { /* microtask flush */ });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/web/reader/records/rr_1/opened',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(refetchMock).toHaveBeenCalledTimes(1);
  });

  it('does NOT call when snapshotStateKind === "loading"', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<ReaderOpenedBeacon recordId="rr_1" snapshotStateKind="loading" />);
    await act(async () => {});
    expect(fetchMock).not.toHaveBeenCalled();
    expect(refetchMock).not.toHaveBeenCalled();
  });

  it('does NOT call when snapshotStateKind === "error"', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<ReaderOpenedBeacon recordId="rr_1" snapshotStateKind="error" />);
    await act(async () => {});
    expect(fetchMock).not.toHaveBeenCalled();
    expect(refetchMock).not.toHaveBeenCalled();
  });

  it('does NOT call when snapshotStateKind === "idle"', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<ReaderOpenedBeacon recordId="rr_1" snapshotStateKind="idle" />);
    await act(async () => {});
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('idempotent under StrictMode double-mount (only one POST)', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    // 模拟 StrictMode：连续两次 effect
    const { rerender } = render(<ReaderOpenedBeacon recordId="rr_1" snapshotStateKind="loaded" />);
    rerender(<ReaderOpenedBeacon recordId="rr_1" snapshotStateKind="loaded" />);
    await act(async () => {});

    // 只有 1 次 POST（firedRef 防止重复）
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(refetchMock).toHaveBeenCalledTimes(1);
  });

  it('fires again when recordId changes after a previous beacon', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { rerender } = render(<ReaderOpenedBeacon recordId="rr_1" snapshotStateKind="loaded" />);
    await act(async () => {});
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenLastCalledWith('/api/web/reader/records/rr_1/opened', expect.anything());

    rerender(<ReaderOpenedBeacon recordId="rr_2" snapshotStateKind="loaded" />);
    await act(async () => {});
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenLastCalledWith('/api/web/reader/records/rr_2/opened', expect.anything());
  });

  it('does NOT refetch on POST failure', async () => {
    const fetchMock = vi.fn(async () => { throw new Error('network'); });
    vi.stubGlobal('fetch', fetchMock);

    render(<ReaderOpenedBeacon recordId="rr_1" snapshotStateKind="loaded" />);
    await act(async () => {});

    expect(refetchMock).not.toHaveBeenCalled();
  });

  it('does NOT refetch on 5xx response', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 503 }));
    vi.stubGlobal('fetch', fetchMock);

    render(<ReaderOpenedBeacon recordId="rr_1" snapshotStateKind="loaded" />);
    await act(async () => {});

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(refetchMock).not.toHaveBeenCalled();
  });

  it('refetches on 2xx response', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    render(<ReaderOpenedBeacon recordId="rr_1" snapshotStateKind="loaded" />);
    await act(async () => {});

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(refetchMock).toHaveBeenCalledTimes(1);
  });
});