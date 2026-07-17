// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from ".";
import { dialogContentVariants } from "./index";

afterEach(cleanup);

/**
 * Settings Dialog Foundation — Dialog primitive contract.
 *
 * Locks the `xl` size variant and the semantic modal z-index migration:
 * - `xl` size emits the Settings Center width/height targets.
 * - `DialogOverlay` resolves z-index via `--app-z-modal-backdrop`.
 * - `DialogContent` resolves z-index via `--app-z-modal` (no hardcoded `z-50`).
 * - Existing `sm`/`md`/`lg` behavior is unchanged.
 */

function renderDialog(size: "sm" | "md" | "lg" | "xl") {
  return render(
    <Dialog open defaultOpen>
      <DialogContent size={size} showCloseButton={false}>
        <DialogTitle>title</DialogTitle>
        <DialogDescription>desc</DialogDescription>
        <div>body</div>
      </DialogContent>
    </Dialog>,
  );
}

describe("dialogContentVariants — size xl", () => {
  it("xl size class includes the Settings Center width and height targets", () => {
    const cls = dialogContentVariants({ size: "xl" });
    expect(cls).toContain("max-w-[min(70rem,calc(100vw-3rem))]");
    expect(cls).toContain("max-h-[min(46rem,calc(100dvh-3rem))]");
  });

  it("xl size renders with width/height arbitrary classes on the content node", () => {
    renderDialog("xl");
    const content = screen.getByText("body").parentElement;
    expect(content).not.toBeNull();
    expect(content!.className).toContain(
      "max-w-[min(70rem,calc(100vw-3rem))]",
    );
    expect(content!.className).toContain(
      "max-h-[min(46rem,calc(100dvh-3rem))]",
    );
  });
});

describe("dialogContentVariants — existing sizes unchanged", () => {
  it("sm size still emits max-w-md", () => {
    expect(dialogContentVariants({ size: "sm" })).toContain("max-w-md");
    expect(dialogContentVariants({ size: "sm" })).not.toContain("max-w-xl");
  });

  it("md size still emits max-w-xl", () => {
    expect(dialogContentVariants({ size: "md" })).toContain("max-w-xl");
  });

  it("lg size still emits max-w-2xl", () => {
    expect(dialogContentVariants({ size: "lg" })).toContain("max-w-2xl");
  });

  it("default variant resolves to md size", () => {
    expect(dialogContentVariants()).toContain("max-w-xl");
  });
});

describe("Dialog semantic z-index migration", () => {
  it("DialogContent resolves z-index via --app-z-modal (no z-50)", () => {
    renderDialog("md");
    const content = screen.getByText("body").parentElement;
    expect(content).not.toBeNull();
    expect(content!.className).toContain("z-[var(--app-z-modal)]");
    expect(content!.className).not.toMatch(/\bz-50\b/);
  });

  it("DialogOverlay resolves z-index via --app-z-modal-backdrop", () => {
    renderDialog("md");
    // The overlay is the first sibling of the content inside the portal.
    // We find it by class via the primitiveOverlay signature.
    const overlay = document.querySelector(
      ".app-overlay.fixed.inset-0",
    ) as HTMLElement | null;
    expect(overlay).not.toBeNull();
    expect(overlay!.className).toContain("z-[var(--app-z-modal-backdrop)]");
  });
});
