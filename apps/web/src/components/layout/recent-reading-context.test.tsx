/** @vitest-environment jsdom */

import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RecentReadingProvider, useRecentReading } from './recent-reading-context';
import type { ReadingRecordListItemVm } from '@/services/bff/reading-records';
import { appReadingRecordRoute } from '@/lib/routes';

function makeItem(id: string): ReadingRecordListItemVm {
  return {
    readingRecordId: id,
    readerUrl: appReadingRecordRoute(id),
    title: `R-${id}`,
    createdAt: '2026-07-14T00:00:00Z',
    sourceType: 'text',
    productState: 'readable_enhancing',
    readinessState: 'article_ready',
    lastEventSequence: 1,
    lastOpenedAt: null,
    sourceLabel: '粘贴文本',
  };
}

function Probe() {
  const { items, refetch } = useRecentReading();
  return (
    <>
      <ul data-testid="list">
        {items.map((i) => (<li key={i.readingRecordId}>{i.title}</li>))}
      </ul>
      <button type="button" onClick={() => void refetch()}>Refetch</button>
    </>
  );
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('RecentReadingProvider', () => {
  it('renders initial items', () => {
    render(
      <RecentReadingProvider initialItems={[makeItem('a'), makeItem('b')]}>
        <Probe />
      </RecentReadingProvider>,
    );
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
  });

  it('refetch() fetches /api/web/reading-records?limit=10 and updates items', async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ ok: true, items: [makeItem('x')], total: 1, limit: 10 }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);

    render(
      <RecentReadingProvider initialItems={[makeItem('a')]}>
        <Probe />
      </RecentReadingProvider>,
    );
    await act(async () => {
      screen.getByRole('button', { name: 'Refetch' }).click();
    });

    await waitFor(() => expect(screen.queryByText('R-a')).toBeNull());
    expect(screen.getByText('R-x')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/web/reading-records?limit=10',
      expect.objectContaining({ cache: 'no-store' }),
    );
  });

  it('refetch() failure leaves items unchanged', async () => {
    const fetchMock = vi.fn(async () => { throw new Error('network'); });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <RecentReadingProvider initialItems={[makeItem('a')]}>
        <Probe />
      </RecentReadingProvider>,
    );
    await act(async () => {
      screen.getByRole('button', { name: 'Refetch' }).click();
    });
    // 等待 microtask flush
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText('R-a')).toBeTruthy();
  });

  it('refetch() with non-ok response leaves items unchanged', async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ ok: false, code: 'upstream_unavailable', message: '' }),
      { status: 503, headers: { 'content-type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);

    render(
      <RecentReadingProvider initialItems={[makeItem('a')]}>
        <Probe />
      </RecentReadingProvider>,
    );
    await act(async () => {
      screen.getByRole('button', { name: 'Refetch' }).click();
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText('R-a')).toBeTruthy();
  });

  it('useRecentReading degrades gracefully outside a provider', async () => {
    function Naked() {
      const { items, refetch } = useRecentReading();
      return (
        <>
          <ul data-testid="list">
            {items.map((i) => (<li key={i.readingRecordId}>{i.title}</li>))}
          </ul>
          <button type="button" onClick={() => void refetch()}>Refetch</button>
        </>
      );
    }
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ ok: true, items: [], total: 0, limit: 0 }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);

    render(<Naked />);
    // No crash, no items, and the no-op refetch should not hit the network.
    expect(screen.queryAllByRole('listitem')).toHaveLength(0);
    await act(async () => {
      screen.getByRole('button', { name: 'Refetch' }).click();
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
