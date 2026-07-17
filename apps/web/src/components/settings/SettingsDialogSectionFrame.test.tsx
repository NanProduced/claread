/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  SettingsDialogSectionFrame,
  type SettingsDialogSectionWidth,
} from "./SettingsDialogSectionFrame";

afterEach(cleanup);

interface FrameOverrides {
  title?: string;
  description?: string;
  width?: SettingsDialogSectionWidth;
}

function renderFrame(overrides: FrameOverrides = {}) {
  return render(
    <SettingsDialogSectionFrame
      title={overrides.title ?? "账户"}
      description={overrides.description}
      width={overrides.width}
    >
      <div data-testid="frame-body">body content</div>
    </SettingsDialogSectionFrame>,
  );
}

/**
 * DOM structure produced by SettingsDialogSectionFrame:
 *
 *   <div class="grid h-full min-h-0 grid-rows-[auto_1fr]">   <- root
 *     <div class="shrink-0 border-b ...">                     <- header
 *       <h2 id="...">title</h2>
 *       <p>description</p> (optional)
 *     </div>
 *     <div class="min-h-0 overflow-y-auto" aria-labelledby>  <- body region
 *       <div class="px-5 py-7 md:px-8 md:py-8 max-w-[34rem]"> <- content wrapper
 *         <div data-testid="frame-body">children</div>       <- children
 *       </div>
 *     </div>
 *   </div>
 */

/** Returns the root grid container that wraps header + body. */
function getRootGrid(): HTMLElement {
  // frame-body -> content wrapper -> body region -> root
  return screen.getByTestId("frame-body").parentElement!.parentElement!
    .parentElement!;
}

/** Returns the fixed header (title + optional description). */
function getHeader(): HTMLElement {
  return getRootGrid().firstElementChild as HTMLElement;
}

/** Returns the scrollable body region (with aria-labelledby). */
function getBodyRegion(): HTMLElement {
  // frame-body -> content wrapper -> body region
  return screen.getByTestId("frame-body").parentElement!.parentElement!;
}

/** Returns the content wrapper inside the body region (carries width + padding). */
function getContentWrapper(): HTMLElement {
  return screen.getByTestId("frame-body").parentElement!;
}

describe("SettingsDialogSectionFrame — header contract", () => {
  it("renders the title inside an h2 (UI sans, not font-headline)", () => {
    renderFrame({ title: "账户" });
    const heading = screen.getByRole("heading", { name: "账户", level: 2 });
    expect(heading).toBeTruthy();
    expect(heading.tagName).toBe("H2");
    // UI sans: must not pull in font-headline (the display family).
    expect(heading.className).not.toMatch(/font-headline/);
    expect(heading.className).toContain("text-ink");
  });

  it("renders the description below the title when provided", () => {
    renderFrame({ title: "账户", description: "管理你的身份信息与登录会话" });
    const desc = screen.getByText("管理你的身份信息与登录会话");
    expect(desc.tagName).toBe("P");
    // Description lives in the same header cell, after the title.
    expect(desc.parentElement).toBe(getHeader());
  });

  it("does not render a description element when description is omitted", () => {
    renderFrame({ title: "账户" });
    expect(getHeader().querySelector("p")).toBeNull();
  });

  it("header is fixed (shrink-0) and separated by a bottom hairline", () => {
    renderFrame();
    const header = getHeader();
    expect(header.className).toContain("shrink-0");
    expect(header.className).toContain("border-b");
    expect(header.className).toContain("border-hairline");
  });
});

describe("SettingsDialogSectionFrame — body scroll contract", () => {
  it("body region has min-h-0 and overflow-y-auto for independent scroll", () => {
    renderFrame();
    const body = getBodyRegion();
    expect(body.className).toContain("min-h-0");
    expect(body.className).toContain("overflow-y-auto");
  });

  it("body region exposes aria-labelledby pointing to the title id", () => {
    renderFrame({ title: "账户" });
    const heading = screen.getByRole("heading", { name: "账户", level: 2 });
    const body = getBodyRegion();
    const titleId = heading.getAttribute("id");
    expect(titleId).toBeTruthy();
    expect(body.getAttribute("aria-labelledby")).toBe(titleId);
  });

  it("root is a full-height grid with two rows (auto header + 1fr body)", () => {
    renderFrame();
    const root = getRootGrid();
    expect(root.className).toContain("h-full");
    expect(root.className).toContain("min-h-0");
    expect(root.className).toContain("grid-rows-[auto_1fr]");
  });
});

describe("SettingsDialogSectionFrame — content width contract", () => {
  it("standard width constrains content column to max-w-[34rem]", () => {
    renderFrame({ width: "standard" });
    const contentWrapper = getContentWrapper();
    expect(contentWrapper.className).toContain("max-w-[34rem]");
  });

  it("wide width does NOT apply the standard column constraint", () => {
    renderFrame({ width: "wide" });
    const contentWrapper = getContentWrapper();
    expect(contentWrapper.className).not.toContain("max-w-[34rem]");
  });

  it("defaults to standard width when width is omitted", () => {
    renderFrame();
    const contentWrapper = getContentWrapper();
    expect(contentWrapper.className).toContain("max-w-[34rem]");
  });

  it("body content padding scales up at md breakpoint (px-5 -> md:px-8)", () => {
    renderFrame();
    const contentWrapper = getContentWrapper();
    expect(contentWrapper.className).toContain("px-5");
    expect(contentWrapper.className).toContain("md:px-8");
  });
});

describe("SettingsDialogSectionFrame — children rendering", () => {
  it("renders children inside the scrollable body", () => {
    renderFrame();
    const body = getBodyRegion();
    expect(body.contains(screen.getByTestId("frame-body"))).toBe(true);
  });

  it("each render produces a stable, unique title id (no collisions across instances)", () => {
    const { unmount } = render(
      <>
        <SettingsDialogSectionFrame title="账户">
          <span data-testid="a">a</span>
        </SettingsDialogSectionFrame>
        <SettingsDialogSectionFrame title="偏好">
          <span data-testid="b">b</span>
        </SettingsDialogSectionFrame>
      </>,
    );
    const headings = screen.getAllByRole("heading", { level: 2 });
    expect(headings).toHaveLength(2);
    const ids = headings.map((h) => h.getAttribute("id"));
    expect(new Set(ids).size).toBe(2);
    unmount();
    cleanup();
  });
});
