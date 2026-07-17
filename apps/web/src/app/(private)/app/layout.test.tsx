/** @vitest-environment node */

import { describe, expect, it, vi } from 'vitest';

vi.mock('server-only', () => ({}));

const recentMock = vi.fn();
vi.mock('@/services/bff/reading-records', () => ({
  getReadingRecordListFromWeb: (...args: unknown[]) => recentMock(...args),
}));

const providerMock = vi.fn((props: { initialItems: unknown[]; children: React.ReactNode }) => (
  <div data-testid="provider" data-items={JSON.stringify(props.initialItems)}>{props.children}</div>
));
vi.mock('@/components/layout/recent-reading-context', () => ({
  RecentReadingProvider: (props: unknown) => providerMock(props as { initialItems: unknown[]; children: React.ReactNode }),
}));

const sessionMock = vi.fn();
vi.mock('@/services/bff/session', () => ({
  getProjectedWebSession: () => sessionMock(),
}));

vi.mock('@/components/layout', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <div data-testid="appshell">{children}</div>,
}));
vi.mock('@/components/providers/CloudPreferencesSync', () => ({
  CloudPreferencesSync: () => null,
}));

import { renderToString } from 'react-dom/server';
import AppShellLayout from './layout';

describe('AppShellLayout', () => {
  it('passes initialItems to RecentReadingProvider on success', async () => {
    recentMock.mockResolvedValue({ ok: true, items: [{ id: 'r1' }], total: 1, limit: 10 });
    sessionMock.mockResolvedValue({ phone: '13800000000' });
    const element = await AppShellLayout({ children: null, settings: null });
    const html = renderToString(element);
    expect(html).toContain('data-testid="provider"');
    expect(html).toContain('data-items="[{&quot;id&quot;:&quot;r1&quot;}]"');
  });

  it('passes [] when BFF returns failure', async () => {
    recentMock.mockResolvedValue({ ok: false, status: 503, code: 'upstream_unavailable', message: '' });
    sessionMock.mockResolvedValue({ phone: null });
    const element = await AppShellLayout({ children: null, settings: null });
    const html = renderToString(element);
    expect(html).toContain('data-items="[]"');
  });

  it('renders settings slot after children', async () => {
    recentMock.mockResolvedValue({ ok: true, items: [], total: 0, limit: 10 });
    sessionMock.mockResolvedValue({ phone: '13800000000' });
    const element = await AppShellLayout({
      children: <div data-testid="children-content">Children</div>,
      settings: <div data-testid="settings-content">Settings</div>,
    });
    const html = renderToString(element);
    const childrenIdx = html.indexOf('data-testid="children-content"');
    const settingsIdx = html.indexOf('data-testid="settings-content"');
    expect(childrenIdx).toBeGreaterThan(-1);
    expect(settingsIdx).toBeGreaterThan(-1);
    // settings slot must appear after children in the rendered HTML
    expect(settingsIdx).toBeGreaterThan(childrenIdx);
  });

  it('renders null settings slot without error', async () => {
    recentMock.mockResolvedValue({ ok: true, items: [], total: 0, limit: 10 });
    sessionMock.mockResolvedValue({ phone: '13800000000' });
    const element = await AppShellLayout({
      children: <div>Children</div>,
      settings: null,
    });
    const html = renderToString(element);
    expect(html).toContain('Children');
  });
});
