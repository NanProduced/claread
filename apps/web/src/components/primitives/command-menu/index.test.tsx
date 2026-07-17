// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { CommandMenuDialog } from "./index";

afterEach(cleanup);

// __dirname = apps/web/src/components/primitives/command-menu
// 4 levels up -> apps/web (so "src/app/globals.css" resolves correctly).
const ROOT = resolve(__dirname, "../../../..");

function readAppFile(rel: string): string {
  return readFileSync(resolve(ROOT, rel), "utf8");
}

function renderDialog() {
  return render(
    <CommandMenuDialog open onOpenChange={() => {}}>
      <div>cmd-body</div>
    </CommandMenuDialog>,
  );
}

describe("CommandMenuDialog — backdrop contract (unified with Settings)", () => {
  it("overlay is forced to no backdrop blur (backdrop-blur-none)", () => {
    renderDialog();
    const overlay = document.querySelector(
      ".app-overlay.fixed.inset-0",
    ) as HTMLElement | null;
    expect(overlay).not.toBeNull();
    expect(overlay!.className).toContain("backdrop-blur-none");
    // The shared primitive default `backdrop-blur-md` must NOT leak through.
    expect(overlay!.className).not.toMatch(/\bbackdrop-blur-md\b/);
  });

  it("overlay resolves z-index via --app-z-modal-backdrop (no hard-coded z-index)", () => {
    renderDialog();
    const overlay = document.querySelector(
      ".app-overlay.fixed.inset-0",
    ) as HTMLElement | null;
    expect(overlay).not.toBeNull();
    expect(overlay!.className).toContain("z-[var(--app-z-modal-backdrop)]");
    // No arbitrary z-9999 / z-50 / z-[9999] hard-codes on the overlay.
    expect(overlay!.className).not.toMatch(/z-9999/);
    expect(overlay!.className).not.toMatch(/\bz-50\b/);
  });

  it("modal backdrop z-index (90) is strictly greater than shell navigation (70)", () => {
    // Source-level guarantee: the globals.css tokens must keep modal-backdrop
    // above shell-navigation so the Sidebar/Reader is dimmed and non-interactive
    // when the command palette opens. This mirrors the Settings shell test.
    const globals = readAppFile("src/app/globals.css");
    expect(globals).toMatch(/--app-z-shell-navigation:\s*70\s*;?/);
    expect(globals).toMatch(/--app-z-modal-backdrop:\s*90\s*;?/);
  });

  it("source explicitly passes overlayClassName=\"backdrop-blur-none\" to DialogContent", () => {
    // Source-level guarantee complementing the runtime assertion above.
    // Guards against accidental removal of the override during refactors.
    const source = readAppFile(
      "src/components/primitives/command-menu/index.tsx",
    );
    expect(source).toContain('overlayClassName="backdrop-blur-none"');
  });

  it("overlay is portalled to document.body (not inside any sidebar container)", () => {
    // Same portal contract as Settings — both modals must portal the
    // overlay to document.body so the z-index token contract
    // (--app-z-modal-backdrop > --app-z-shell-navigation) takes effect
    // regardless of where the trigger lives.
    renderDialog();
    const overlay = document.querySelector(
      ".app-overlay.fixed.inset-0",
    ) as HTMLElement | null;
    expect(overlay).not.toBeNull();
    expect(overlay!.closest("body")).not.toBeNull();
    expect(overlay!.closest("[data-app-sidebar]")).toBeNull();
  });

  it("overlay does not carry pointer-events-none (must intercept clicks)", () => {
    renderDialog();
    const overlay = document.querySelector(
      ".app-overlay.fixed.inset-0",
    ) as HTMLElement | null;
    expect(overlay).not.toBeNull();
    expect(overlay!.className).not.toContain("pointer-events-none");
  });

  it("dialog content does not add a redundant blur on top of the no-blur overlay", () => {
    renderDialog();
    const dialog = screen.getByRole("dialog");
    // The dialog surface itself must not introduce a competing backdrop-filter.
    expect(dialog.className).not.toMatch(/backdrop-blur-md/);
    expect(dialog.className).not.toMatch(/backdrop-blur-lg/);
    expect(dialog.className).not.toMatch(/backdrop-blur-xl/);
  });
});

describe("CommandMenuDialog — close behavior preservation", () => {
  it("does not render a visible close button (showCloseButton={false})", () => {
    renderDialog();
    // The CommandMenuDialog relies on Esc + overlay click + item selection;
    // it intentionally does not render the default DialogContent close button.
    const closeBtns = screen.queryAllByRole("button", { name: "关闭对话框" });
    expect(closeBtns).toHaveLength(0);
  });

  it("renders the dialog title for accessibility (sr-only)", () => {
    renderDialog();
    // Title is sr-only but still resolvable by role for AT users.
    const heading = screen.getByRole("heading", { name: "命令面板" });
    expect(heading).toBeTruthy();
    expect(heading.className).toContain("sr-only");
  });
});
