/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  READER_ASK_PRESENTATION_CONSTANTS,
  readerAskColumnWidthPx,
  readerAskPresentationCssVars,
  readerAskRequiredWorkspaceWidthPx,
  useReaderAskPresentation,
  type ReaderAskSurface,
} from "./useReaderAskPresentation";

// --- ResizeObserver mock -------------------------------------------------

interface ROEntry {
  contentRect: { width: number; height: number };
  target: Element;
}
type ROCallback = (entries: ROEntry[]) => void;

class MockResizeObserver {
  static instances: MockResizeObserver[] = [];
  static last: MockResizeObserver | null = null;
  callback: ROCallback;
  observed: Element[] = [];
  disconnected = false;
  constructor(cb: ROCallback) {
    this.callback = cb;
    MockResizeObserver.instances.push(this);
    MockResizeObserver.last = this;
  }
  observe(el: Element) {
    this.observed.push(el);
    this.disconnected = false;
  }
  unobserve(el: Element) {
    this.observed = this.observed.filter((e) => e !== el);
  }
  disconnect() {
    this.disconnected = true;
    this.observed = [];
  }
  trigger(width: number) {
    this.callback([
      { contentRect: { width, height: 0 }, target: this.observed[0] },
    ]);
  }
}

// --- helpers -------------------------------------------------------------

function setElementWidth(el: HTMLElement, width: number) {
  el.getBoundingClientRect = () =>
    ({
      width,
      height: 0,
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: width,
      bottom: 0,
      toJSON() {
        return {};
      },
    }) as DOMRect;
}

function makeWorkspace(width = 1400): HTMLElement {
  const el = document.createElement("div");
  setElementWidth(el, width);
  return el;
}

// jsdom defaults document.documentElement.clientWidth to 0 (no layout), so the
// hook's viewport tracking resolves to 0 unless overridden. At viewport 0, the
// 29vw Ask column clamps to its floor (24rem=384px), making the required
// workspace width (48+2.5)*16 + 384 = 1192px — the same floor-only threshold.
const TEST_VIEWPORT_NARROW = 0;
const REQUIRED_PX = readerAskRequiredWorkspaceWidthPx(TEST_VIEWPORT_NARROW);

// --- setup ---------------------------------------------------------------

beforeEach(() => {
  MockResizeObserver.instances = [];
  MockResizeObserver.last = null;
  vi.stubGlobal("ResizeObserver", MockResizeObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  // Reset any inline root font size set by individual tests.
  document.documentElement.style.fontSize = "";
});

// --- pure helpers --------------------------------------------------------

describe("readerAskRequiredWorkspaceWidthPx", () => {
  it("sums the reading area, outline gutter, and viewport-aware Ask column", () => {
    const { minimumReadingAreaRem, outlineGutterRem } =
      READER_ASK_PRESENTATION_CONSTANTS;
    const askFloor = readerAskColumnWidthPx(0);
    expect(readerAskRequiredWorkspaceWidthPx(0)).toBe(
      (minimumReadingAreaRem + outlineGutterRem) * 16 + askFloor,
    );
    // At viewport 0 the Ask column clamps to its floor (24rem=384px):
    // (48 + 2.5) * 16 + 384 = 1192.
    expect(readerAskRequiredWorkspaceWidthPx(0)).toBe(1192);
  });

  it("grows with the viewport because the Ask column uses 25.5vw", () => {
    // At viewport 1920, the Ask column is 25.5vw = 489.6px (between floor and
    // ceiling), so the required width exceeds the floor-only threshold.
    const required1920 = readerAskRequiredWorkspaceWidthPx(1920);
    expect(required1920).toBeGreaterThan(1192);
    expect(required1920).toBe(
      (48 + 2.5) * 16 + readerAskColumnWidthPx(1920),
    );

    // At viewport 3000, the Ask column clamps to its ceiling (32rem=512px).
    const required3000 = readerAskRequiredWorkspaceWidthPx(3000);
    expect(required3000).toBe((48 + 2.5) * 16 + 512);
  });

  it("respects a non-default rem size", () => {
    expect(readerAskRequiredWorkspaceWidthPx(0, undefined, 20)).toBe(
      (48 + 2.5) * 20 + 24 * 20,
    );
  });
});

describe("readerAskColumnWidthPx", () => {
  it("clamps to [24rem, 32rem] using 25.5vw as the ideal", () => {
    const floor = 24 * 16;
    const ceil = 32 * 16;
    // Narrow viewport: 25.5vw < floor → floor.
    expect(readerAskColumnWidthPx(1000)).toBe(floor);
    // Mid viewport: 25.5vw between floor and ceil → ideal.
    expect(readerAskColumnWidthPx(1920)).toBe(
      Math.min(ceil, Math.max(floor, 0.255 * 1920)),
    );
    // Wide viewport: 25.5vw > ceil → ceil.
    expect(readerAskColumnWidthPx(3000)).toBe(ceil);
  });
});

describe("readerAskPresentationCssVars", () => {
  it("exposes centralized CSS custom properties for the Plate", () => {
    const vars = readerAskPresentationCssVars() as Record<string, string>;
    expect(vars["--reader-ask-minimum-reading-area"]).toBe("48rem");
    expect(vars["--reader-ask-outline-gutter"]).toBe("2.5rem");
    expect(vars["--reader-ask-column-min"]).toBe("24rem");
    expect(vars["--reader-ask-column-ideal"]).toBe("25.5vw");
    expect(vars["--reader-ask-column-max"]).toBe("32rem");
    expect(vars["--reader-ask-column-width"]).toBe(
      "clamp(24rem, 25.5vw, 32rem)",
    );
  });
});

// --- hook ----------------------------------------------------------------

describe("useReaderAskPresentation", () => {
  it("docks sidecar when the workspace has safe capacity", () => {
    const el = makeWorkspace(REQUIRED_PX + 200);
    const { result } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: el,
      }),
    );
    expect(result.current.hasSidecarCapacity).toBe(true);
    expect(result.current.effectiveSurface).toBe("sidecar");
    expect(result.current.requiredWorkspaceWidthPx).toBe(REQUIRED_PX);
    expect(MockResizeObserver.last?.observed).toContain(el);
  });

  it("falls back to floating when the workspace is too narrow", () => {
    const el = makeWorkspace(REQUIRED_PX - 200);
    const { result } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: el,
      }),
    );
    expect(result.current.hasSidecarCapacity).toBe(false);
    expect(result.current.effectiveSurface).toBe("floating");
  });

  it("falls back to floating when workspace exceeds the floor threshold but not the viewport-aware threshold", () => {
    // At viewport 1920, the 25.5vw Ask column is 489.6px, making the required
    // workspace (48+2.5)*16 + 489.6 = 1297.6px. A workspace of 1200px exceeds
    // the floor-only threshold (1192px) but NOT the viewport-aware threshold.
    const wideViewport = 1920;
    const requiredAtWide = readerAskRequiredWorkspaceWidthPx(wideViewport);
    const workspaceWidth = 1200;
    expect(workspaceWidth).toBeGreaterThan(REQUIRED_PX);
    expect(workspaceWidth).toBeLessThan(requiredAtWide);

    const el = makeWorkspace(workspaceWidth);
    const { result } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: el,
        options: { viewportWidthPx: wideViewport },
      }),
    );
    expect(result.current.hasSidecarCapacity).toBe(false);
    expect(result.current.effectiveSurface).toBe("floating");
    expect(result.current.requiredWorkspaceWidthPx).toBe(requiredAtWide);
  });

  it("recomputes capacity when viewport width changes the 25.5vw Ask column", () => {
    // Narrow viewport: Ask column at floor, required=1192. Workspace 1200px
    // → capacity true → sidecar.
    const el = makeWorkspace(1200);
    let currentViewport = 1000;
    const { result, rerender } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: el,
        options: { viewportWidthPx: currentViewport },
      }),
    );
    expect(result.current.hasSidecarCapacity).toBe(true);
    expect(result.current.effectiveSurface).toBe("sidecar");

    // Wide viewport: Ask column = 25.5vw of 1920 = 489.6px, required = 1297.6px.
    // Workspace 1200px → capacity false → floating.
    currentViewport = 1920;
    rerender();
    expect(result.current.hasSidecarCapacity).toBe(false);
    expect(result.current.effectiveSurface).toBe("floating");

    // Narrow again: capacity restored without changing the request.
    currentViewport = 1000;
    rerender();
    expect(result.current.hasSidecarCapacity).toBe(true);
    expect(result.current.effectiveSurface).toBe("sidecar");
  });

  it("recovers to sidecar automatically when capacity is restored", () => {
    const el = makeWorkspace(REQUIRED_PX - 100);
    const { result } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: el,
      }),
    );
    expect(result.current.effectiveSurface).toBe("floating");

    // Widen the workspace past the threshold.
    act(() => {
      setElementWidth(el, REQUIRED_PX + 100);
      MockResizeObserver.last!.trigger(REQUIRED_PX + 100);
    });
    expect(result.current.hasSidecarCapacity).toBe(true);
    expect(result.current.effectiveSurface).toBe("sidecar");
  });

  it("keeps an explicit floating request floating at every width", () => {
    const el = makeWorkspace(REQUIRED_PX + 600);
    const { result } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "floating",
        workspaceEl: el,
      }),
    );
    // Capacity is still measured true, but effective stays floating.
    expect(result.current.hasSidecarCapacity).toBe(true);
    expect(result.current.effectiveSurface).toBe("floating");

    // Even if the workspace later narrows, floating remains floating.
    act(() => {
      setElementWidth(el, REQUIRED_PX - 100);
      MockResizeObserver.last!.trigger(REQUIRED_PX - 100);
    });
    expect(result.current.hasSidecarCapacity).toBe(false);
    expect(result.current.effectiveSurface).toBe("floating");
  });

  it("never overwrites the requested surface (derived only)", () => {
    const el = makeWorkspace(REQUIRED_PX - 50);
    const { result, rerender } = renderHook(
      ({ requestedSurface }: { requestedSurface: ReaderAskSurface }) =>
        useReaderAskPresentation({
          requestedSurface,
          workspaceEl: el,
        }),
      { initialProps: { requestedSurface: "sidecar" as ReaderAskSurface } },
    );
    // Insufficient capacity: effective floats while the request stays sidecar.
    expect(result.current.effectiveSurface).toBe("floating");
    rerender({ requestedSurface: "sidecar" });
    expect(result.current.effectiveSurface).toBe("floating");

    // Capacity returns: recovers to sidecar without the caller changing the
    // request.
    act(() => {
      setElementWidth(el, REQUIRED_PX + 50);
      MockResizeObserver.last!.trigger(REQUIRED_PX + 50);
    });
    expect(result.current.effectiveSurface).toBe("sidecar");
  });

  it("does not re-render when a resize stays on the same side of the threshold", () => {
    let renderCount = 0;
    const el = makeWorkspace(REQUIRED_PX + 200);
    const { result } = renderHook(() => {
      renderCount++;
      return useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: el,
      });
    });
    // Initial render + layout-effect state update (false → true) = 2 renders.
    const baseline = renderCount;
    expect(baseline).toBeGreaterThanOrEqual(2);
    expect(result.current.hasSidecarCapacity).toBe(true);

    // Same-side resize: no capacity transition, no state update.
    act(() => {
      setElementWidth(el, REQUIRED_PX + 260);
      MockResizeObserver.last!.trigger(REQUIRED_PX + 260);
    });
    expect(result.current.hasSidecarCapacity).toBe(true);
    expect(renderCount).toBe(baseline);

    // Cross-threshold resize: state update → one extra render.
    act(() => {
      setElementWidth(el, REQUIRED_PX - 80);
      MockResizeObserver.last!.trigger(REQUIRED_PX - 80);
    });
    expect(result.current.hasSidecarCapacity).toBe(false);
    expect(renderCount).toBe(baseline + 1);
  });

  it("treats a missing workspace element as no capacity and creates no observer", () => {
    const { result } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: null,
      }),
    );
    expect(result.current.hasSidecarCapacity).toBe(false);
    expect(result.current.effectiveSurface).toBe("floating");
    expect(MockResizeObserver.instances).toHaveLength(0);
  });

  it("establishes the observer after the workspace element transitions from null to an element", () => {
    // Simulates the callback-ref + useState pattern: the hook first sees null
    // (before mount commit), then the element arrives via re-render.
    let currentEl: HTMLElement | null = null;
    const { rerender, result } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: currentEl,
      }),
    );
    // Before mount: no capacity, no observer.
    expect(result.current.hasSidecarCapacity).toBe(false);
    expect(result.current.effectiveSurface).toBe("floating");
    expect(MockResizeObserver.instances).toHaveLength(0);

    // Simulate callback ref firing after mount: element becomes available.
    const el = makeWorkspace(REQUIRED_PX + 200);
    currentEl = el;
    rerender();

    expect(MockResizeObserver.instances).toHaveLength(1);
    expect(MockResizeObserver.last?.observed).toContain(el);
    expect(result.current.hasSidecarCapacity).toBe(true);
    expect(result.current.effectiveSurface).toBe("sidecar");
  });

  it("disconnects the ResizeObserver on unmount", () => {
    const el = makeWorkspace(REQUIRED_PX + 100);
    const { unmount } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: el,
      }),
    );
    const observer = MockResizeObserver.last!;
    expect(observer.disconnected).toBe(false);
    unmount();
    expect(observer.disconnected).toBe(true);
  });

  it("disconnects the old observer and re-observes when the workspace element changes", () => {
    const elA = makeWorkspace(REQUIRED_PX + 100);
    let currentEl: HTMLElement = elA;
    const { rerender } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: currentEl,
      }),
    );
    const observerA = MockResizeObserver.last!;
    expect(observerA.observed).toContain(elA);

    const elB = makeWorkspace(REQUIRED_PX + 300);
    currentEl = elB;
    rerender();

    expect(observerA.disconnected).toBe(true);
    const observerB = MockResizeObserver.last!;
    expect(observerB).not.toBe(observerA);
    expect(observerB.observed).toContain(elB);
  });

  it("uses the measured workspace element, not window.innerWidth, for capacity", () => {
    // A wide viewport but a narrow workspace element must still fall back.
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      get: () => 2400,
    });
    const el = makeWorkspace(REQUIRED_PX - 300);
    const { result } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: el,
        options: { viewportWidthPx: 2400 },
      }),
    );
    expect(result.current.hasSidecarCapacity).toBe(false);
    expect(result.current.effectiveSurface).toBe("floating");
    // askColumnWidthPx still follows the viewport clamp, independent of capacity.
    expect(result.current.askColumnWidthPx).toBe(
      readerAskColumnWidthPx(2400),
    );
  });

  it("reads the root font size at runtime for rem conversion", () => {
    // Set a non-default root font size. The hook should pick it up via
    // getComputedStyle instead of using the 16px constant.
    document.documentElement.style.fontSize = "20px";
    const remPx = 20;
    const requiredAt20 = readerAskRequiredWorkspaceWidthPx(0, undefined, remPx);
    const el = makeWorkspace(requiredAt20 + 40);
    const { result } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: el,
      }),
    );
    expect(result.current.hasSidecarCapacity).toBe(true);
    expect(result.current.requiredWorkspaceWidthPx).toBe(requiredAt20);
    // A workspace just below the 20px-rem threshold should float.
    const narrowEl = makeWorkspace(requiredAt20 - 40);
    const { result: narrowResult } = renderHook(() =>
      useReaderAskPresentation({
        requestedSurface: "sidecar",
        workspaceEl: narrowEl,
      }),
    );
    expect(narrowResult.current.hasSidecarCapacity).toBe(false);
    expect(narrowResult.current.effectiveSurface).toBe("floating");
  });
});
