/** @vitest-environment jsdom */

import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// --- Mocks for next/navigation ---
const mockReplace = vi.fn();
const mockBack = vi.fn();
let mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: mockReplace,
    back: mockBack,
    push: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  useSearchParams: () => mockSearchParams,
}));

// --- Mock SettingsDialogShell to inspect props ---
const mockShell = vi.fn();
vi.mock("@/components/settings/SettingsDialogShell", () => ({
  SettingsDialogShell: (props: unknown) => {
    mockShell(props);
    const {
      children,
      open,
      onOpenChange,
      activeSection,
      onSectionChange,
      onCloseAutoFocus,
    } = props as {
      children: React.ReactNode;
      open: boolean;
      onOpenChange: (open: boolean) => void;
      activeSection: string;
      onSectionChange: (section: string) => void;
      onCloseAutoFocus?: (event: Event) => void;
    };
    return (
      <div
        data-testid="shell"
        data-open={open}
        data-section={activeSection}
        tabIndex={-1}
      >
        <button data-testid="close-btn" onClick={() => onOpenChange(false)}>
          close
        </button>
        <button
          data-testid="section-btn"
          data-section={activeSection}
          onClick={() => onSectionChange("usage")}
        >
          switch
        </button>
        <button
          data-testid="autofocus-btn"
          onClick={() => onCloseAutoFocus?.(new Event("focus"))}
        >
          autofocus
        </button>
        {children}
      </div>
    );
  },
}));

// --- Mock SettingsSectionContent to inspect props ---
const mockContent = vi.fn();
vi.mock("@/app/(private)/app/settings/sections/SettingsSectionContent", () => ({
  SettingsSectionContent: (props: unknown) => {
    mockContent(props);
    const { section } = props as { section: string };
    return <div data-testid="content" data-section={section} />;
  },
}));

import { SettingsDialogRouteClient, parseSettingsSection } from "./SettingsDialogRouteClient";

afterEach(() => {
  cleanup();
  // Remove any opener buttons left by focus-capture tests
  document.querySelectorAll("button[id^='opener-']").forEach((el) => el.remove());
  mockReplace.mockClear();
  mockBack.mockClear();
  mockShell.mockClear();
  mockContent.mockClear();
  mockSearchParams = new URLSearchParams();
});

beforeEach(() => {
  mockSearchParams = new URLSearchParams();
});

describe("parseSettingsSection", () => {
  it("returns 'preferences' for null/undefined", () => {
    expect(parseSettingsSection(null)).toBe("preferences");
    expect(parseSettingsSection(undefined)).toBe("preferences");
  });

  it("returns the section for valid values", () => {
    expect(parseSettingsSection("account")).toBe("account");
    expect(parseSettingsSection("preferences")).toBe("preferences");
    expect(parseSettingsSection("usage")).toBe("usage");
    expect(parseSettingsSection("support")).toBe("support");
  });

  it("falls back to 'preferences' for invalid values", () => {
    expect(parseSettingsSection("invalid")).toBe("preferences");
    expect(parseSettingsSection("")).toBe("preferences");
    expect(parseSettingsSection("settings")).toBe("preferences");
    expect(parseSettingsSection("ACCOUNT")).toBe("preferences");
  });
});

describe("SettingsDialogRouteClient", () => {
  const defaultProps = {
    accountData: {
      nickname: "Test",
      displayFallback: "Test",
      phone: "13800000000",
      status: "ready" as const,
      avatarText: "T",
    },
    preferencesData: {
      readingGoal: "balanced" as never,
      readingVariant: "translation" as never,
      canEdit: true,
    },
  };

  it("renders SettingsDialogShell with open=true", () => {
    render(<SettingsDialogRouteClient {...defaultProps} />);
    expect(mockShell).toHaveBeenCalled();
    const shellProps = mockShell.mock.calls[0][0] as { open: boolean };
    expect(shellProps.open).toBe(true);
  });

  it("defaults activeSection to 'preferences' when no query param", () => {
    render(<SettingsDialogRouteClient {...defaultProps} />);
    expect(screen.getByTestId("shell").dataset.section).toBe("preferences");
  });

  it("reads activeSection from ?section= query param", () => {
    mockSearchParams.set("section", "account");
    render(<SettingsDialogRouteClient {...defaultProps} />);
    expect(screen.getByTestId("shell").dataset.section).toBe("account");
  });

  it("supports all four valid section values", () => {
    for (const section of ["account", "preferences", "usage", "support"] as const) {
      cleanup();
      mockSearchParams = new URLSearchParams(`section=${section}`);
      render(<SettingsDialogRouteClient {...defaultProps} />);
      expect(screen.getByTestId("shell").dataset.section).toBe(section);
    }
  });

  it("falls back to 'preferences' for invalid ?section= value", () => {
    mockSearchParams.set("section", "invalid");
    render(<SettingsDialogRouteClient {...defaultProps} />);
    expect(screen.getByTestId("shell").dataset.section).toBe("preferences");
  });

  it("does not pass usageData or usageShowLedger to SettingsSectionContent", () => {
    render(<SettingsDialogRouteClient {...defaultProps} />);
    expect(mockContent).toHaveBeenCalled();
    const contentProps = mockContent.mock.calls[0][0] as {
      usageData?: unknown;
      usageShowLedger?: boolean;
    };
    expect(contentProps.usageData).toBeUndefined();
    expect(contentProps.usageShowLedger).toBeUndefined();
  });

  it("calls router.replace when section changes (no history accumulation)", () => {
    render(<SettingsDialogRouteClient {...defaultProps} />);
    fireEvent.click(screen.getByTestId("section-btn"));
    expect(mockReplace).toHaveBeenCalledTimes(1);
    const replacedUrl = mockReplace.mock.calls[0][0] as string;
    expect(replacedUrl).toContain("/app/settings?");
    expect(replacedUrl).toContain("section=usage");
  });

  it("calls router.back when dialog closes (close button)", () => {
    render(<SettingsDialogRouteClient {...defaultProps} />);
    fireEvent.click(screen.getByTestId("close-btn"));
    expect(mockBack).toHaveBeenCalledTimes(1);
  });

  it("does NOT call router.back when dialog opens (open=true)", () => {
    render(<SettingsDialogRouteClient {...defaultProps} />);
    // open is always true; onOpenChange is only triggered by close
    // Verify back was not called during initial render
    expect(mockBack).not.toHaveBeenCalled();
  });
});

describe("SettingsDialogRouteClient — opener focus capture & restoration", () => {
  const defaultProps = {
    accountData: {
      nickname: "Test",
      displayFallback: "Test",
      phone: "13800000000",
      status: "ready" as const,
      avatarText: "T",
    },
    preferencesData: {
      readingGoal: "balanced" as never,
      readingVariant: "translation" as never,
      canEdit: true,
    },
  };

  /** Helper: create a focusable button, append to body, and focus it. */
  function createAndFocusOpener(id: string): HTMLButtonElement {
    const btn = document.createElement("button");
    btn.id = id;
    btn.textContent = id;
    document.body.appendChild(btn);
    btn.focus();
    return btn;
  }

  /** Extract onCloseAutoFocus from the last Shell mock call. */
  function getLastOnCloseAutoFocus(): (event: Event) => void {
    const shellProps = mockShell.mock.calls.at(-1)![0] as {
      onCloseAutoFocus?: (event: Event) => void;
    };
    expect(shellProps.onCloseAutoFocus).toBeDefined();
    return shellProps.onCloseAutoFocus!;
  }

  it("passes onCloseAutoFocus to SettingsDialogShell", () => {
    render(<SettingsDialogRouteClient {...defaultProps} />);
    const cb = getLastOnCloseAutoFocus();
    expect(typeof cb).toBe("function");
  });

  it("captures the focused element on first render (before Radix auto-focus)", () => {
    const opener = createAndFocusOpener("opener-1");
    expect(document.activeElement).toBe(opener);

    render(<SettingsDialogRouteClient {...defaultProps} />);

    // Simulate Radix close: call the captured onCloseAutoFocus
    const event = new Event("focus", { bubbles: false });
    const preventDefault = vi.spyOn(event, "preventDefault");
    getLastOnCloseAutoFocus()(event);

    // Focus should have been restored to the opener
    expect(document.activeElement).toBe(opener);
    expect(preventDefault).toHaveBeenCalled();

    document.body.removeChild(opener);
  });

  it("does NOT call focus() on a disconnected element", () => {
    const opener = createAndFocusOpener("opener-2");
    expect(document.activeElement).toBe(opener);

    render(<SettingsDialogRouteClient {...defaultProps} />);

    // Remove opener from DOM (simulating underlying page unmounting
    // or the opener being conditionally rendered away)
    document.body.removeChild(opener);
    expect(opener.isConnected).toBe(false);

    const event = new Event("focus", { bubbles: false });
    const preventDefault = vi.spyOn(event, "preventDefault");
    getLastOnCloseAutoFocus()(event);

    // preventDefault must NOT be called when element is disconnected
    expect(preventDefault).not.toHaveBeenCalled();
  });

  it("does not overwrite opener ref on re-render (Dialog internal focus migrations)", () => {
    const opener = createAndFocusOpener("opener-3");
    expect(document.activeElement).toBe(opener);

    const { rerender } = render(<SettingsDialogRouteClient {...defaultProps} />);

    // Simulate Radix auto-focusing dialog content (activeElement changes
    // after first render due to Dialog's focus trap)
    const dialogContent = screen.getByTestId("shell");
    dialogContent.focus();
    expect(document.activeElement).toBe(dialogContent);

    // Re-render with same props — opener ref must NOT be overwritten
    rerender(<SettingsDialogRouteClient {...defaultProps} />);

    // Simulate close: focus should still go to the original opener
    const event = new Event("focus", { bubbles: false });
    getLastOnCloseAutoFocus()(event);
    expect(document.activeElement).toBe(opener);

    document.body.removeChild(opener);
  });

  it("does not capture body as opener (falls back to null)", () => {
    // Ensure body is the active element (no specific element focused)
    document.body.focus();
    expect(document.activeElement).toBe(document.body);

    render(<SettingsDialogRouteClient {...defaultProps} />);

    // onCloseAutoFocus should still exist but do nothing
    const event = new Event("focus", { bubbles: false });
    const preventDefault = vi.spyOn(event, "preventDefault");
    getLastOnCloseAutoFocus()(event);

    // No opener was captured, so preventDefault should not be called
    expect(preventDefault).not.toHaveBeenCalled();
  });
});
