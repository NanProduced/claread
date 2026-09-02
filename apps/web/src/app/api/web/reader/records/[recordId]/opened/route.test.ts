import { describe, expect, it, vi } from 'vitest';

vi.mock('server-only', () => ({}));

const sessionMock = vi.fn();
vi.mock('@/services/bff/session', () => ({
  getWebSession: () => sessionMock(),
}));

const upstreamMock = vi.fn();
vi.mock('@/services/api/reading-records', () => ({
  markReaderRecordOpened: (...args: unknown[]) => upstreamMock(...args),
}));

import { POST } from './route';

describe('POST /api/web/reader/records/:recordId/opened', () => {
  it('returns 401 for anonymous sessions', async () => {
    sessionMock.mockResolvedValue({ kind: 'anonymous', source: 'none' });

    const res = await POST({} as never, {
      params: Promise.resolve({ recordId: 'rr_1' }),
    });

    expect(res.status).toBe(401);
    expect(upstreamMock).not.toHaveBeenCalled();
  });

  it('proxies to upstream and returns the DTO on authenticated session', async () => {
    sessionMock.mockResolvedValue({
      kind: 'authenticated',
      sessionToken: 'tok',
      source: 'cookie',
    });
    upstreamMock.mockResolvedValue({
      ok: true,
      data: { record_id: 'rr_1', last_opened_at: '2026-07-14T12:00:00Z' },
    });

    const res = await POST({} as never, {
      params: Promise.resolve({ recordId: 'rr_1' }),
    });

    expect(res.status).toBe(200);
    expect(upstreamMock).toHaveBeenCalledWith('tok', 'rr_1');
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toMatchObject({
      ok: true,
      record_id: 'rr_1',
      last_opened_at: '2026-07-14T12:00:00Z',
    });
  });

  it('maps upstream 5xx errors to 503', async () => {
    sessionMock.mockResolvedValue({
      kind: 'authenticated',
      sessionToken: 'tok',
      source: 'cookie',
    });
    upstreamMock.mockResolvedValue({
      ok: false,
      status: 500,
      message: 'internal error',
    });

    const res = await POST({} as never, {
      params: Promise.resolve({ recordId: 'rr_1' }),
    });

    expect(res.status).toBe(503);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.ok).toBe(false);
    expect(body.status).toBe(503);
    expect(body.code).toBe('upstream_unavailable');
  });

  it('maps upstream network failures (status 0) to 503 upstream_unavailable', async () => {
    sessionMock.mockResolvedValue({
      kind: 'authenticated',
      sessionToken: 'tok',
      source: 'cookie',
    });
    upstreamMock.mockResolvedValue({
      ok: false,
      status: 0,
      message: 'network',
    });

    const res = await POST({} as never, {
      params: Promise.resolve({ recordId: 'rr_1' }),
    });

    expect(res.status).toBe(503);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.ok).toBe(false);
    expect(body.status).toBe(503);
    expect(body.code).toBe('upstream_unavailable');
  });

  it('passes through upstream 4xx errors', async () => {
    sessionMock.mockResolvedValue({
      kind: 'authenticated',
      sessionToken: 'tok',
      source: 'cookie',
    });
    upstreamMock.mockResolvedValue({
      ok: false,
      status: 404,
      message: 'not found',
    });

    const res = await POST({} as never, {
      params: Promise.resolve({ recordId: 'rr_missing' }),
    });

    expect(res.status).toBe(404);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.ok).toBe(false);
    expect(body.status).toBe(404);
    expect(body.code).toBe('upstream_error');
  });
});
