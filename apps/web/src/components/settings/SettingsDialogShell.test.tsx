// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  SettingsDialogShell,
  type SettingsSection,
} from "./SettingsDialogShell";

afterEach(cleanup);

const ROOT = resolve(__dirname, "../../..");

function readAppFile(rel: string): string {
  return readFileSync(resolve(ROOT, rel), "utf8");
}

interface RenderOverrides {
  open?: boolean;
  activeSection?: SettingsSection;
  overlayClassName?: string;
}

function renderShell(overrides: RenderOverrides = {}) {
  const onSectionChange = vi.fn<(section: SettingsSection) => void>();
  const onOpenChange = vi.fn<(open: boolean) => void>();
  render(
    <SettingsDialogShell
      open={overrides.open ?? true}
      onOpenChange={onOpenChange}
      activeSection={overrides.activeSection ?? "account"}
      onSectionChange={onSectionChange}
      overlayClassName={overrides.overlayClassName}
    >
      <div>shell-body</div>
    </SettingsDialogShell>,
  );
  return { onSectionChange, onOpenChange };
}

/** Returns the 4 destination section buttons inside the settings nav, in render order. */
function getSectionButtons(): HTMLElement[] {
  const nav = screen.getByRole("navigation", { name: "设置分区" });
  return within(nav).getAllByRole("button");
}

function getDialog(): HTMLElement {
  return screen.getByRole("dialog");
}

describe("SettingsDialogShell — controlled open/close", () => {
  it("renders shell content when open=true", () => {
    renderShell();
    expect(
      screen.getByRole("navigation", { name: "设置分区" }),
    ).toBeTruthy();
    expect(screen.getByText("shell-body")).toBeTruthy();
  });

  it("does not render shell content when open=false", () => {
    renderShell({ open: false });
    expect(
      screen.queryByRole("navigation", { name: "设置分区" }),
    ).toBeNull();
    expect(screen.queryByText("shell-body")).toBeNull();
  });
});

describe("SettingsDialogShell — section navigation semantics", () => {
  it("does NOT use ARIA tabs model (no role=tablist/tab/tabpanel)", () => {
    renderShell();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    expect(screen.queryByRole("tabpanel")).toBeNull();
  });

  it("renders 4 destination buttons in order: 个人资料 / 偏好 / 用量与积分 / 支持", () => {
    renderShell();
    const buttons = getSectionButtons();
    expect(buttons).toHaveLength(4);
    expect(buttons[0].textContent).toBe("个人资料");
    expect(buttons[1].textContent).toBe("偏好");
    expect(buttons[2].textContent).toBe("用量与积分");
    expect(buttons[3].textContent).toBe("支持");
  });

  it("section id remains 'account' for the 个人资料 label", () => {
    const { onSectionChange } = renderShell({ activeSection: "preferences" });
    const buttons = getSectionButtons();
    fireEvent.click(buttons[0]);
    expect(onSectionChange).toHaveBeenCalledWith("account");
  });

  it("section id remains 'usage' for the 用量与积分 label", () => {
    const { onSectionChange } = renderShell({ activeSection: "account" });
    const buttons = getSectionButtons();
    fireEvent.click(buttons[2]);
    expect(onSectionChange).toHaveBeenCalledWith("usage");
  });

  it("calls onSectionChange with the clicked section id", () => {
    const { onSectionChange } = renderShell({ activeSection: "account" });
    const buttons = getSectionButtons();
    fireEvent.click(buttons[1]);
    expect(onSectionChange).toHaveBeenCalledTimes(1);
    expect(onSectionChange).toHaveBeenCalledWith("preferences");
  });

  it("marks only the active section with aria-current=page", () => {
    renderShell({ activeSection: "preferences" });
    const buttons = getSectionButtons();
    expect(buttons[0].getAttribute("aria-current")).toBeNull();
    expect(buttons[1].getAttribute("aria-current")).toBe("page");
    expect(buttons[2].getAttribute("aria-current")).toBeNull();
    expect(buttons[3].getAttribute("aria-current")).toBeNull();
  });

  it("does not use aria-selected (removed from ARIA tabs model)", () => {
    renderShell({ activeSection: "account" });
    const buttons = getSectionButtons();
    for (const btn of buttons) {
      expect(btn.hasAttribute("aria-selected")).toBe(false);
    }
  });

  it("uses --app-control-current on the active section (not a blue accent)", () => {
    renderShell({ activeSection: "usage" });
    const buttons = getSectionButtons();
    expect(buttons[2].className).toContain("bg-[var(--app-control-current)]");
    // Selected nav item must not rely on blue/accent tokens.
    expect(buttons[2].className).not.toMatch(/lens-blue/);
    expect(buttons[2].className).not.toMatch(/action-primary/);
  });

  it("uses --interactive-quiet-hover on non-active button hover", () => {
    renderShell({ activeSection: "account" });
    const buttons = getSectionButtons();
    expect(buttons[1].className).toContain(
      "hover:bg-[var(--interactive-quiet-hover)]",
    );
  });
});

describe("SettingsDialogShell — rail information architecture", () => {
  it("renders two rail groups with quiet labels 账户 and Claread", () => {
    renderShell();
    const nav = screen.getByRole("navigation", { name: "设置分区" });
    expect(within(nav).getByText("账户")).toBeTruthy();
    expect(within(nav).getByText("Claread")).toBeTruthy();
  });

  it("renders each destination with a 16px lucide icon", () => {
    renderShell();
    const nav = screen.getByRole("navigation", { name: "设置分区" });
    const buttons = within(nav).getAllByRole("button");
    expect(buttons).toHaveLength(4);
    const icons = nav.querySelectorAll("svg");
    expect(icons.length).toBeGreaterThanOrEqual(4);
    for (const btn of buttons) {
      expect(btn.querySelector("svg")).not.toBeNull();
    }
  });

  it("does not render uppercase eyebrow group labels", () => {
    renderShell();
    const nav = screen.getByRole("navigation", { name: "设置分区" });
    const labels = nav.querySelectorAll("div");
    for (const label of labels) {
      expect(label.className).not.toMatch(/uppercase/);
    }
  });

  it("does not render an account profile entry in the rail", () => {
    renderShell();
    expect(screen.queryByText("Alex")).toBeNull();
    const buttons = getSectionButtons();
    expect(buttons).toHaveLength(4);
  });

  it("keeps the account section id while exposing the 个人资料 UI label", () => {
    const { onSectionChange } = renderShell({ activeSection: "preferences" });
    const profileBtn = screen.getByRole("button", { name: "个人资料" });
    fireEvent.click(profileBtn);
    expect(onSectionChange).toHaveBeenCalledWith("account");
  });
});

describe("SettingsDialogShell — DialogContent primitive override regression", () => {
  it("uses !flex to override Dialog primitive default grid layout", () => {
    renderShell();
    const dialog = getDialog();
    expect(dialog.className).toContain("!flex");
  });

  it("keeps the dialog itself fixed above the backdrop", () => {
    renderShell();
    const dialog = getDialog();
    // `relative` would override DialogContent's base `fixed` utility and
    // place the dialog after the overlay in normal document flow.
    expect(dialog.className).toContain("!fixed");
    expect(dialog.className).not.toMatch(/\brelative\b/);
  });

  it("uses !gap-0 to override Dialog primitive default gap-4", () => {
    renderShell();
    const dialog = getDialog();
    expect(dialog.className).toContain("!gap-0");
  });

  it("uses !p-0 to override Dialog primitive default padding", () => {
    renderShell();
    const dialog = getDialog();
    expect(dialog.className).toContain("!p-0");
  });

  it("keeps static surface overrides (!bg-none / !bg-surface / !shadow-none)", () => {
    renderShell();
    const dialog = getDialog();
    expect(dialog.className).toContain("!bg-none");
    expect(dialog.className).toContain("!bg-surface");
    expect(dialog.className).toContain("!shadow-none");
  });
});

describe("SettingsDialogShell — responsive layout", () => {
  it("desktop layout includes a left navigation (w-[12rem] and bg-surface-raised)", () => {
    renderShell();
    const nav = screen.getByRole("navigation", { name: "设置分区" });
    expect(nav.className).toContain("w-[12rem]");
    expect(nav.className).toContain("bg-surface-raised");
    // Desktop nav is separated from content by a hairline border.
    expect(nav.className).toContain("md:border-r");
    expect(nav.className).toContain("border-hairline");
  });

  it("mobile layout is full-screen (h-dvh and rounded-none)", () => {
    renderShell();
    const dialog = getDialog();
    expect(dialog.className).toContain("h-dvh");
    expect(dialog.className).toContain("rounded-none");
  });

  it("mobile nav is a top horizontal section nav (flex-row + overflow-x-auto)", () => {
    renderShell();
    const nav = screen.getByRole("navigation", { name: "设置分区" });
    // Mobile: horizontal scrolling nav (flex + overflow-x-auto).
    expect(nav.className).toContain("overflow-x-auto");
    // Desktop overrides to vertical column.
    expect(nav.className).toContain("md:flex-col");
  });

  it("right panel is a relative flex container for the section frame", () => {
    // The Shell no longer provides its own scroll body — the
    // SettingsDialogSectionFrame (passed as children) provides the
    // fixed header + scrollable body. The Shell's right panel is just
    // a structural container.
    renderShell();
    const dialog = getDialog();
    const rightPanel = dialog.querySelector(".relative.flex.min-h-0.flex-1.flex-col");
    expect(rightPanel).not.toBeNull();
    // Children render inside the right panel.
    expect(rightPanel?.textContent).toContain("shell-body");
  });
});

describe("SettingsDialogShell — centered desktop geometry", () => {
  it("does not top-anchor the desktop dialog (removed md:!top-6 / md:!translate-y-0)", () => {
    renderShell();
    const dialog = getDialog();
    expect(dialog.className).not.toMatch(/(?:^| )md:!top-6/);
    expect(dialog.className).not.toMatch(/(?:^| )md:!translate-y-0/);
  });

  it("constrains desktop width to min(76rem, calc(100vw - 4rem))", () => {
    renderShell();
    const dialog = getDialog();
    expect(dialog.className).toContain(
      "!w-[min(76rem,calc(100vw-4rem))]",
    );
    expect(dialog.className).toContain(
      "!max-w-[min(76rem,calc(100vw-4rem))]",
    );
  });

  it("constrains desktop height to min(60rem, calc(100dvh - 4rem))", () => {
    renderShell();
    const dialog = getDialog();
    expect(dialog.className).toContain(
      "h-[min(60rem,calc(100dvh-4rem))]",
    );
    expect(dialog.className).toContain(
      "!max-h-[min(60rem,calc(100dvh-4rem))]",
    );
  });
});

describe("SettingsDialogShell — stable frame contract", () => {
  it("desktop has a fixed viewport-relative height (not just max-h)", () => {
    renderShell();
    const dialog = getDialog();
    // Fixed height ensures the dialog frame stays stable when switching
    // between sections with different content lengths.
    expect(dialog.className).toContain("h-[min(60rem,calc(100dvh-4rem))]");
  });

  it("mobile overrides to full-screen h-dvh", () => {
    renderShell();
    const dialog = getDialog();
    expect(dialog.className).toContain("max-md:h-dvh");
  });

  it("dialog has overflow-hidden so content never escapes the frame", () => {
    renderShell();
    const dialog = getDialog();
    expect(dialog.className).toContain("overflow-hidden");
  });

  it("all four sections share the same fixed outer frame", () => {
    // Render with each section active and verify the dialog className
    // (which encodes the frame dimensions) is identical across sections.
    // renderShell doesn't expose unmount; cleanup() between renders
    // detaches the previous DOM tree so each getByRole call is unambiguous.
    renderShell({ activeSection: "account" });
    const dialog1 = getDialog().className;
    cleanup();

    renderShell({ activeSection: "preferences" });
    const dialog2 = getDialog().className;
    cleanup();

    renderShell({ activeSection: "usage" });
    const dialog3 = getDialog().className;
    cleanup();

    renderShell({ activeSection: "support" });
    const dialog4 = getDialog().className;

    expect(dialog1).toBe(dialog2);
    expect(dialog2).toBe(dialog3);
    expect(dialog3).toBe(dialog4);
  });
});

describe("SettingsDialogShell — touch targets (mobile 44px minimum)", () => {
  it("renders two close buttons: mobile size-11 in fixed bar, desktop size-9 absolute", () => {
    // The Shell splits the close button into two dedicated elements so each
    // belongs to fixed chrome and never overlaps scroll content:
    //   - mobile: size-11 (44px) inside the top close bar. The bar (parent)
    //     carries `md:hidden`; the button itself just carries size-11.
    //   - desktop: size-9 absolutely positioned in the right panel header,
    //     carries `hidden md:inline-flex` directly so it only shows on md+.
    renderShell();
    const closeBtns = screen.getAllByRole("button", { name: "关闭设置" });
    expect(closeBtns).toHaveLength(2);

    const mobileClose = closeBtns.find((btn) =>
      btn.className.includes("size-11"),
    );
    const desktopClose = closeBtns.find((btn) =>
      btn.className.includes("size-9"),
    );
    expect(mobileClose).toBeDefined();
    expect(desktopClose).toBeDefined();
    // Mobile close lives in the dedicated close bar — the parent carries
    // `md:hidden` so the entire bar (button included) is hidden on desktop.
    const mobileBar = mobileClose!.parentElement!;
    expect(mobileBar.className).toContain("md:hidden");
    // Desktop close is absolute and only shows on md+ (hidden md:inline-flex).
    expect(desktopClose!.className).toContain("absolute");
    expect(desktopClose!.className).toContain("hidden");
    expect(desktopClose!.className).toContain("md:inline-flex");
  });

  it("each section button has max-md:min-h-11 (44px) on mobile", () => {
    renderShell();
    const buttons = getSectionButtons();
    for (const btn of buttons) {
      expect(btn.className).toContain("max-md:min-h-11");
    }
  });
});

describe("SettingsDialogShell — accessibility", () => {
  it("provides an accessible name via DialogTitle", () => {
    renderShell();
    expect(screen.getByRole("heading", { name: "设置" })).toBeTruthy();
  });

  it("provides a description via DialogDescription", () => {
    renderShell();
    expect(
      screen.getByText("管理账户、偏好、用量与积分与支持选项。"),
    ).toBeTruthy();
  });

  it("both close buttons have aria-label '关闭设置'", () => {
    // Two close buttons (mobile bar + desktop absolute) must both expose
    // the same accessible name so AT users can close from either chrome.
    renderShell();
    const closeBtns = screen.getAllByRole("button", { name: "关闭设置" });
    expect(closeBtns).toHaveLength(2);
    for (const btn of closeBtns) {
      expect(btn.getAttribute("aria-label")).toBe("关闭设置");
    }
  });

  it("overlay is forced to no backdrop blur", () => {
    renderShell();
    const overlay = document.querySelector(
      ".app-overlay.fixed.inset-0",
    ) as HTMLElement | null;
    expect(overlay).not.toBeNull();
    expect(overlay!.className).toContain("backdrop-blur-none");
    expect(overlay!.className).not.toMatch(/\bbackdrop-blur-md\b/);
  });

  it("overlay is portalled to document.body (not inside any sidebar container)", () => {
    // Radix DialogPortal renders into document.body by default. This
    // assertion guards against custom portal containers that would
    // inherit the sidebar's stacking context and break the z-index
    // contract verified by the E2E backdrop-contract spec.
    renderShell();
    const overlay = document.querySelector(
      ".app-overlay.fixed.inset-0",
    ) as HTMLElement | null;
    expect(overlay).not.toBeNull();
    expect(overlay!.closest("body")).not.toBeNull();
    // The overlay must NOT be a descendant of any sidebar element.
    expect(overlay!.closest("[data-app-sidebar]")).toBeNull();
  });

  it("overlay does not carry pointer-events-none (must intercept clicks)", () => {
    renderShell();
    const overlay = document.querySelector(
      ".app-overlay.fixed.inset-0",
    ) as HTMLElement | null;
    expect(overlay).not.toBeNull();
    expect(overlay!.className).not.toContain("pointer-events-none");
  });

  it("applies motion-reduce fallback on the dialog content", () => {
    renderShell();
    const dialog = getDialog();
    expect(dialog.className).toContain("motion-reduce:transition-none");
    expect(dialog.className).toContain("motion-reduce:duration-0");
  });
});

describe("SettingsDialogShell — onCloseAutoFocus forwarding", () => {
  it("accepts onCloseAutoFocus prop without error", () => {
    const onCloseAutoFocus = vi.fn();
    render(
      <SettingsDialogShell
        open
        onOpenChange={vi.fn()}
        activeSection="account"
        onSectionChange={vi.fn()}
        onCloseAutoFocus={onCloseAutoFocus}
      >
        <div>shell-body</div>
      </SettingsDialogShell>,
    );
    expect(screen.getByText("shell-body")).toBeTruthy();
  });

  it("source forwards onCloseAutoFocus to DialogContent", () => {
    // Source-level guarantee that the prop is passed to DialogContent,
    // complementing the behavior test above. Radix's onCloseAutoFocus
    // may not reliably fire in jsdom, so this assertion ensures the
    // wiring is present regardless of jsdom focus-event limitations.
    const source = readAppFile(
      "src/components/settings/SettingsDialogShell.tsx",
    );
    expect(source).toContain("onCloseAutoFocus={onCloseAutoFocus}");
    // The prop must be declared in the interface
    expect(source).toMatch(/onCloseAutoFocus\?:\s*\(event: Event\)\s*=>\s*void/);
  });
});

// ---------------------------------------------------------------------------
// Static contract: forbidden patterns in SettingsDialogShell.tsx source.
// Mirrors the reader-ask-theme-convergence.test.ts pattern: read the file
// from disk and assert forbidden style patterns are absent.
// ---------------------------------------------------------------------------

const SHELL_SOURCE = readAppFile(
  "src/components/settings/SettingsDialogShell.tsx",
);

const FORBIDDEN_PATTERNS: ReadonlyArray<{ re: RegExp; label: string }> = [
  { re: /app-panel-surface/, label: "app-panel-surface custom class" },
  { re: /gradient/, label: "gradient reference" },
  { re: /#[0-9a-fA-F]{3,8}/, label: "raw HEX color" },
  { re: /rgba\(/, label: "raw rgba() color value" },
  { re: /reader-annotation/, label: "reader-annotation token reference" },
  { re: /vocab-amber/, label: "vocab-amber token reference" },
  { re: /phrase-lavender/, label: "phrase-lavender token reference" },
  { re: /grammar-violet/, label: "grammar-violet token reference" },
  { re: /structure-green/, label: "structure-green token reference" },
  { re: /context-blue/, label: "context-blue token reference" },
];

describe("SettingsDialogShell — static surface contract", () => {
  it.each(FORBIDDEN_PATTERNS)(
    "source does not contain $label",
    ({ re, label }) => {
      expect(
        re.test(SHELL_SOURCE),
        `forbidden pattern "${label}" still present in SettingsDialogShell.tsx`,
      ).toBe(false);
    },
  );
});

// ---------------------------------------------------------------------------
// z-index regression: globals.css must define the semantic modal z-index
// tokens in the correct order (shell 80 < backdrop 90 < modal 100).
// ---------------------------------------------------------------------------

const GLOBALS_CSS = readAppFile("src/app/globals.css");

describe("globals.css — semantic z-index ordering", () => {
  it("defines --app-z-shell-overlay: 80", () => {
    expect(GLOBALS_CSS).toMatch(/--app-z-shell-overlay:\s*80\s*;?/);
  });

  it("defines --app-z-modal-backdrop: 90", () => {
    expect(GLOBALS_CSS).toMatch(/--app-z-modal-backdrop:\s*90\s*;?/);
  });

  it("defines --app-z-modal: 100", () => {
    expect(GLOBALS_CSS).toMatch(/--app-z-modal:\s*100\s*;?/);
  });

  it("preserves strict ordering shell(80) < backdrop(90) < modal(100)", () => {
    // Include the colon to avoid --app-z-modal matching the prefix of
    // --app-z-modal-backdrop.
    const shellIdx = GLOBALS_CSS.indexOf("--app-z-shell-overlay:");
    const backdropIdx = GLOBALS_CSS.indexOf("--app-z-modal-backdrop:");
    const modalIdx = GLOBALS_CSS.indexOf("--app-z-modal:");
    expect(shellIdx).toBeGreaterThan(-1);
    expect(backdropIdx).toBeGreaterThan(-1);
    expect(modalIdx).toBeGreaterThan(-1);
    // Definitions must appear in ascending z-index order in the :root block.
    expect(shellIdx).toBeLessThan(backdropIdx);
    expect(backdropIdx).toBeLessThan(modalIdx);
  });
});
