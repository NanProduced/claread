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
  // Remove any opener buttons, user-menu triggers, or their test wrappers
  document.querySelectorAll("button[id^='opener-']").forEach((el) => el.remove());
  document
    .querySelectorAll(
      '[data-mobile-user-menu-trigger="true"], [data-desktop-user-menu-trigger="true"]',
    )
    .forEach((el) => el.remove());
  document.querySelectorAll('[data-test-focus-wrapper="true"]').forEach((el) => el.remove());
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

  /** Helper: create a user-menu trigger button with the expected data attribute. */
  function createTrigger(
    variant: "mobile" | "desktop",
    options?: { disabled?: boolean; hidden?: boolean },
  ): HTMLButtonElement {
    const btn = document.createElement("button");
    btn.setAttribute(
      variant === "mobile"
        ? "data-mobile-user-menu-trigger"
        : "data-desktop-user-menu-trigger",
      "true",
    );
    btn.textContent = `${variant} trigger`;
    if (options?.disabled) btn.disabled = true;
    if (options?.hidden) btn.style.display = "none";
    document.body.appendChild(btn);
    return btn;
  }

  /** Helper: create a trigger wrapped in a hidden ancestor (for ancestor-visibility tests). */
  function createTriggerInHiddenAncestor(
    variant: "mobile" | "desktop",
    hiddenBy: "display" | "visibility" | "hidden-attribute",
  ): { trigger: HTMLButtonElement; wrapper: HTMLDivElement } {
    const wrapper = document.createElement("div");
    wrapper.setAttribute("data-test-focus-wrapper", "true");
    if (hiddenBy === "display") wrapper.style.display = "none";
    if (hiddenBy === "visibility") wrapper.style.visibility = "hidden";
    if (hiddenBy === "hidden-attribute") wrapper.hidden = true;

    const trigger = document.createElement("button");
    trigger.setAttribute(
      variant === "mobile"
        ? "data-mobile-user-menu-trigger"
        : "data-desktop-user-menu-trigger",
      "true",
    );
    trigger.textContent = `${variant} trigger`;
    wrapper.appendChild(trigger);
    document.body.appendChild(wrapper);
    return { trigger, wrapper };
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

  it("does NOT call focus() on a disconnected element when no fallback exists", () => {
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

    // No fallback trigger exists, so preventDefault must NOT be called
    expect(preventDefault).not.toHaveBeenCalled();
  });

  it("focuses the mobile trigger when opener is disconnected", () => {
    const opener = createAndFocusOpener("opener-fallback-mobile");
    render(<SettingsDialogRouteClient {...defaultProps} />);
    document.body.removeChild(opener);

    const mobileTrigger = createTrigger("mobile");
    const event = new Event("focus", { bubbles: false });
    const preventDefault = vi.spyOn(event, "preventDefault");
    getLastOnCloseAutoFocus()(event);

    expect(document.activeElement).toBe(mobileTrigger);
    expect(preventDefault).toHaveBeenCalled();

    document.body.removeChild(mobileTrigger);
  });

  it("focuses the desktop trigger when opener is disconnected and mobile trigger is absent", () => {
    const opener = createAndFocusOpener("opener-fallback-desktop");
    render(<SettingsDialogRouteClient {...defaultProps} />);
    document.body.removeChild(opener);

    const desktopTrigger = createTrigger("desktop");
    const event = new Event("focus", { bubbles: false });
    const preventDefault = vi.spyOn(event, "preventDefault");
    getLastOnCloseAutoFocus()(event);

    expect(document.activeElement).toBe(desktopTrigger);
    expect(preventDefault).toHaveBeenCalled();

    document.body.removeChild(desktopTrigger);
  });

  it("prefers mobile trigger over desktop trigger when opener is disconnected", () => {
    const opener = createAndFocusOpener("opener-prefer-mobile");
    render(<SettingsDialogRouteClient {...defaultProps} />);
    document.body.removeChild(opener);

    const desktopTrigger = createTrigger("desktop");
    const mobileTrigger = createTrigger("mobile");
    const event = new Event("focus", { bubbles: false });
    getLastOnCloseAutoFocus()(event);

    expect(document.activeElement).toBe(mobileTrigger);

    document.body.removeChild(mobileTrigger);
    document.body.removeChild(desktopTrigger);
  });

  it("does not use a disabled mobile trigger as fallback", () => {
    const opener = createAndFocusOpener("opener-disabled-fallback");
    render(<SettingsDialogRouteClient {...defaultProps} />);
    document.body.removeChild(opener);

    const disabledMobile = createTrigger("mobile", { disabled: true });
    const desktopTrigger = createTrigger("desktop");
    const event = new Event("focus", { bubbles: false });
    const preventDefault = vi.spyOn(event, "preventDefault");
    getLastOnCloseAutoFocus()(event);

    expect(document.activeElement).toBe(desktopTrigger);
    expect(preventDefault).toHaveBeenCalled();

    document.body.removeChild(disabledMobile);
    document.body.removeChild(desktopTrigger);
  });

  it("does not use a hidden mobile trigger as fallback", () => {
    const opener = createAndFocusOpener("opener-hidden-fallback");
    render(<SettingsDialogRouteClient {...defaultProps} />);
    document.body.removeChild(opener);

    const hiddenMobile = createTrigger("mobile", { hidden: true });
    const desktopTrigger = createTrigger("desktop");
    const event = new Event("focus", { bubbles: false });
    const preventDefault = vi.spyOn(event, "preventDefault");
    getLastOnCloseAutoFocus()(event);

    expect(document.activeElement).toBe(desktopTrigger);
    expect(preventDefault).toHaveBeenCalled();

    document.body.removeChild(hiddenMobile);
    document.body.removeChild(desktopTrigger);
  });

  it("ignores mobile trigger inside ancestor display:none and uses desktop trigger", () => {
    const opener = createAndFocusOpener("opener-ancestor-display");
    render(<SettingsDialogRouteClient {...defaultProps} />);
    document.body.removeChild(opener);

    createTriggerInHiddenAncestor("mobile", "display");
    const desktopTrigger = createTrigger("desktop");
    const event = new Event("focus", { bubbles: false });
    const preventDefault = vi.spyOn(event, "preventDefault");
    getLastOnCloseAutoFocus()(event);

    expect(document.activeElement).toBe(desktopTrigger);
    expect(preventDefault).toHaveBeenCalled();

    document.body.removeChild(desktopTrigger);
  });

  it("ignores mobile trigger inside ancestor visibility:hidden and uses desktop trigger", () => {
    const opener = createAndFocusOpener("opener-ancestor-visibility");
    render(<SettingsDialogRouteClient {...defaultProps} />);
    document.body.removeChild(opener);

    createTriggerInHiddenAncestor("mobile", "visibility");
    const desktopTrigger = createTrigger("desktop");
    const event = new Event("focus", { bubbles: false });
    const preventDefault = vi.spyOn(event, "preventDefault");
    getLastOnCloseAutoFocus()(event);

    expect(document.activeElement).toBe(desktopTrigger);
    expect(preventDefault).toHaveBeenCalled();

    document.body.removeChild(desktopTrigger);
  });

  it("ignores mobile trigger inside ancestor with hidden attribute and uses desktop trigger", () => {
    const opener = createAndFocusOpener("opener-ancestor-hidden-attr");
    render(<SettingsDialogRouteClient {...defaultProps} />);
    document.body.removeChild(opener);

    createTriggerInHiddenAncestor("mobile", "hidden-attribute");
    const desktopTrigger = createTrigger("desktop");
    const event = new Event("focus", { bubbles: false });
    const preventDefault = vi.spyOn(event, "preventDefault");
    getLastOnCloseAutoFocus()(event);

    expect(document.activeElement).toBe(desktopTrigger);
    expect(preventDefault).toHaveBeenCalled();

    document.body.removeChild(desktopTrigger);
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
