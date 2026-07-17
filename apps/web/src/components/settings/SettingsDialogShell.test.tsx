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

/** Returns the 4 section buttons inside the settings nav, in render order. */
function getSectionButtons(): HTMLElement[] {
  const nav = screen.getByRole("navigation", { name: "设置分区" });
  return within(nav).getAllByRole("button");
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

  it("renders 4 section buttons in order: 账户 / 偏好 / 用量与积分 / 支持", () => {
    renderShell();
    const buttons = getSectionButtons();
    expect(buttons).toHaveLength(4);
    expect(buttons[0].textContent).toBe("账户");
    expect(buttons[1].textContent).toBe("偏好");
    expect(buttons[2].textContent).toBe("用量与积分");
    expect(buttons[3].textContent).toBe("支持");
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

describe("SettingsDialogShell — DialogContent primitive override regression", () => {
  it("uses !flex to override Dialog primitive default grid layout", () => {
    renderShell();
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("!flex");
  });

  it("uses !gap-0 to override Dialog primitive default gap-4", () => {
    renderShell();
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("!gap-0");
  });

  it("uses !p-0 to override Dialog primitive default padding", () => {
    renderShell();
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("!p-0");
  });

  it("keeps static surface overrides (!bg-none / !bg-surface / !shadow-none)", () => {
    renderShell();
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("!bg-none");
    expect(dialog.className).toContain("!bg-surface");
    expect(dialog.className).toContain("!shadow-none");
  });
});

describe("SettingsDialogShell — responsive layout", () => {
  it("desktop layout includes a left navigation (w-[13.5rem] and bg-surface-raised)", () => {
    renderShell();
    const nav = screen.getByRole("navigation", { name: "设置分区" });
    expect(nav.className).toContain("w-[13.5rem]");
    expect(nav.className).toContain("bg-surface-raised");
    // Desktop nav is separated from content by a hairline border.
    expect(nav.className).toContain("md:border-r");
    expect(nav.className).toContain("border-hairline");
  });

  it("mobile layout is full-screen (h-dvh and rounded-none)", () => {
    renderShell();
    const dialog = screen.getByRole("dialog");
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

  it("content panel uses static bg-surface and scrolls independently", () => {
    renderShell();
    const dialog = screen.getByRole("dialog");
    // Content panel is the flex-1 child with bg-surface + overflow-y-auto.
    const content = dialog.querySelector(".bg-surface.overflow-y-auto");
    expect(content).not.toBeNull();
  });
});

describe("SettingsDialogShell — touch targets (mobile 44px minimum)", () => {
  it("close button is size-9 on desktop but size-11 (44px) on mobile", () => {
    renderShell();
    const closeBtn = screen.getByRole("button", { name: "关闭设置" });
    expect(closeBtn.className).toContain("size-9");
    expect(closeBtn.className).toContain("max-md:size-11");
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

  it("close button has aria-label '关闭设置'", () => {
    renderShell();
    const closeBtn = screen.getByRole("button", { name: "关闭设置" });
    expect(closeBtn).toBeTruthy();
    expect(closeBtn.getAttribute("aria-label")).toBe("关闭设置");
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

  it("applies motion-reduce fallback on the dialog content", () => {
    renderShell();
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("motion-reduce:transition-none");
    expect(dialog.className).toContain("motion-reduce:duration-0");
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
