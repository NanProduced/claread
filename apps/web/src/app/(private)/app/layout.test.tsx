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

// Mock the SettingsDialogProvider so the SSR test doesn't pull in
// Radix Dialog client components (which would add client-only side effects).
// The mock records that children were wrapped, so we can assert the
// Provider is mounted at the AppShell level.
const settingsDialogProviderMock = vi.fn((props: { children: React.ReactNode }) => (
  <div data-testid="settings-dialog-provider">{props.children}</div>
));
vi.mock('@/components/settings/SettingsDialogProvider', () => ({
  SettingsDialogProvider: (props: unknown) =>
    settingsDialogProviderMock(props as { children: React.ReactNode }),
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

  it('wraps the AppShell in SettingsDialogProvider so any nested client component can open Settings', async () => {
    recentMock.mockResolvedValue({ ok: true, items: [], total: 0, limit: 10 });
    sessionMock.mockResolvedValue({ phone: '13800000000' });
    const element = await AppShellLayout({
      children: <div data-testid="children-content">Children</div>,
      settings: null,
    });
    const html = renderToString(element);
    // The provider wrapper is present.
    expect(html).toContain('data-testid="settings-dialog-provider"');
    // The Provider wraps the AppShell, so the appshell markup appears
    // AFTER the provider's opening tag.
    const providerIdx = html.indexOf('data-testid="settings-dialog-provider"');
    const appShellIdx = html.indexOf('data-testid="appshell"');
    expect(appShellIdx).toBeGreaterThan(providerIdx);
    // Children render inside the AppShell, inside the Provider.
    const childrenIdx = html.indexOf('data-testid="children-content"');
    expect(childrenIdx).toBeGreaterThan(appShellIdx);
    // Provider was called with children (not settings slot).
    expect(settingsDialogProviderMock).toHaveBeenCalled();
    const lastCall = settingsDialogProviderMock.mock.calls.at(-1)![0] as {
      children: React.ReactNode;
    };
    expect(lastCall.children).toBeDefined();
  });
});
