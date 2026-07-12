/** @vitest-environment jsdom */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SidebarRail } from ".";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
    className?: string;
    [key: string]: unknown;
  }) => (
    <a href={href} className={className} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/image", () => ({
  default: (props: { alt?: string; className?: string }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img alt={props.alt ?? ""} className={props.className} />
  ),
}));

vi.mock("../command-palette", () => ({
  useCommandPalette: () => vi.fn(),
}));

vi.mock("@/lib/shortcuts", () => ({
  formatShortcut: () => "⌘K",
}));

afterEach(() => {
  cleanup();
});

describe("SidebarRail z-index contract", () => {
  it("uses the semantic shell-navigation z-index for the overlay surface in all sidebar states", () => {
    const { container, rerender } = render(
      <SidebarRail pathname="/app/reader-record/record_1" sidebarMode="closed" />,
    );

    const sidebar = container.querySelector<HTMLElement>('[data-app-sidebar="rail"]');
    expect(sidebar).not.toBeNull();
    expect(sidebar?.style.zIndex).toBe("var(--app-z-shell-navigation)");

    // Overlay state.
    rerender(
      <SidebarRail pathname="/app/reader-record/record_1" sidebarMode="overlay" />,
    );
    const overlaySidebar = container.querySelector<HTMLElement>(
      '[data-app-sidebar="rail"]',
    );
    expect(overlaySidebar?.style.zIndex).toBe("var(--app-z-shell-navigation)");

    // Locked state.
    rerender(
      <SidebarRail pathname="/app/reader-record/record_1" sidebarMode="locked" />,
    );
    const lockedSidebar = container.querySelector<HTMLElement>(
      '[data-app-sidebar="rail"]',
    );
    expect(lockedSidebar?.style.zIndex).toBe("var(--app-z-shell-navigation)");
  });
});
