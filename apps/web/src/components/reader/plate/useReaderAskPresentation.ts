import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { CSSProperties } from "react";

/**
 * User-visible Ask presentation form. `sidecar` docks the Ask column beside the
 * article in normal document flow; `floating` overlays it at the viewport
 * bottom-right. The hook never mutates the requested value — it only derives
 * the effective form from the request plus measured capacity.
 */
export type ReaderAskSurface = "sidecar" | "floating";

/**
 * Design tokens for the Reader Ask presentation. Centralized here so
 * React and CSS share a single source of truth; the Plate can inject these as
 * CSS custom properties on the workspace container via
 * {@link readerAskPresentationCssVars} instead of repeating literals.
 */
export interface ReaderAskPresentationConstants {
  /** Minimum reading area width, in rem. */
  readonly minimumReadingAreaRem: number;
  /** Outline gutter width, in rem. */
  readonly outlineGutterRem: number;
  /** Ask column minimum (floor) width, in rem. */
  readonly askColumnMinRem: number;
  /** Ask column ideal width, in vw. */
  readonly askColumnIdealVw: number;
  /** Ask column maximum (ceiling) width, in rem. */
  readonly askColumnMaxRem: number;
}

export const READER_ASK_PRESENTATION_CONSTANTS: ReaderAskPresentationConstants = {
  minimumReadingAreaRem: 48,
  outlineGutterRem: 2.5,
  askColumnMinRem: 24,
  askColumnIdealVw: 25.5,
  askColumnMaxRem: 32,
};

/** Default pixels-per-rem. Browsers default to 16px at the :root font-size. */
export const READER_ASK_REM_PX = 16;

export interface ReaderAskPresentationOptions {
  /**
   * Pixels per rem. When omitted, the hook reads the root element's computed
   * font size at mount. Exposed as an override for tests.
   */
  remPx?: number;
  /**
   * Viewport width in px used for the vw branch of the Ask column clamp.
   * When omitted, the hook tracks `document.documentElement.clientWidth` via
   * a window resize listener.
   */
  viewportWidthPx?: number;
}

export interface ReaderAskPresentationInput {
  requestedSurface: ReaderAskSurface;
  /** The real Reader workspace element to measure. `null` before mount. */
  workspaceEl: HTMLElement | null;
  options?: ReaderAskPresentationOptions;
}

export interface ReaderAskPresentationResult {
  /** Derived presentation decision. Never mutates the requested surface. */
  effectiveSurface: ReaderAskSurface;
  /** True when the measured workspace can safely dock the sidecar. */
  hasSidecarCapacity: boolean;
  /** Required workspace width in px to safely dock sidecar. */
  requiredWorkspaceWidthPx: number;
  /** Computed Ask column width in px for the current viewport (clamp result). */
  askColumnWidthPx: number;
}

/**
 * Compute the px width reserved by the Ask column for a given viewport, using
 * the `clamp(24rem, 25.5vw, 32rem)` rule.
 */
export function readerAskColumnWidthPx(
  viewportWidthPx: number,
  constants: ReaderAskPresentationConstants = READER_ASK_PRESENTATION_CONSTANTS,
  remPx: number = READER_ASK_REM_PX,
): number {
  const min = constants.askColumnMinRem * remPx;
  const ideal = (constants.askColumnIdealVw * viewportWidthPx) / 100;
  const max = constants.askColumnMaxRem * remPx;
  return Math.min(max, Math.max(min, ideal));
}

/**
 * Compute the minimum workspace width in px required to safely dock the
 * sidecar: minimum reading area + outline gutter + the viewport-aware Ask
 * column width (not just the floor). The Ask column grows with the viewport
 * via the 25.5vw branch of the clamp, so a workspace that docks at a narrow
 * viewport may need to float at a wider one.
 */
export function readerAskRequiredWorkspaceWidthPx(
  viewportWidthPx: number,
  constants: ReaderAskPresentationConstants = READER_ASK_PRESENTATION_CONSTANTS,
  remPx: number = READER_ASK_REM_PX,
): number {
  return (
    (constants.minimumReadingAreaRem + constants.outlineGutterRem) * remPx +
    readerAskColumnWidthPx(viewportWidthPx, constants, remPx)
  );
}

/**
 * CSS custom properties the Plate can inject on the workspace container so the
 * sidecar grid and Ask docked column read the same centralized tokens instead
 * of repeating literals in component classes. Returns a fresh object each call.
 */
export function readerAskPresentationCssVars(
  constants: ReaderAskPresentationConstants = READER_ASK_PRESENTATION_CONSTANTS,
): CSSProperties {
  return {
    "--reader-ask-minimum-reading-area": `${constants.minimumReadingAreaRem}rem`,
    "--reader-ask-outline-gutter": `${constants.outlineGutterRem}rem`,
    "--reader-ask-column-min": `${constants.askColumnMinRem}rem`,
    "--reader-ask-column-ideal": `${constants.askColumnIdealVw}vw`,
    "--reader-ask-column-max": `${constants.askColumnMaxRem}rem`,
    "--reader-ask-column-width": `clamp(${constants.askColumnMinRem}rem, ${constants.askColumnIdealVw}vw, ${constants.askColumnMaxRem}rem)`,
  } as CSSProperties;
}

// useLayoutEffect runs before paint so the first measurement settles without a
// floating→sidecar flash; fall back to useEffect during SSR.
const useIsoLayoutEffect =
  typeof window !== "undefined" ? useLayoutEffect : useEffect;

function resolveViewportWidthPx(fallback?: number): number {
  if (typeof fallback === "number") return fallback;
  if (typeof document !== "undefined" && document.documentElement) {
    return document.documentElement.clientWidth;
  }
  return 0;
}

function resolveRemPx(override?: number): number {
  if (typeof override === "number") return override;
  if (typeof window !== "undefined" && typeof document !== "undefined" && document.documentElement) {
    try {
      const rootFontSize = window.getComputedStyle(document.documentElement).fontSize;
      const parsed = parseFloat(rootFontSize);
      if (Number.isFinite(parsed) && parsed > 0) return parsed;
    } catch {
      // getComputedStyle unavailable — fall back to default.
    }
  }
  return READER_ASK_REM_PX;
}

function measureWorkspaceWidth(el: HTMLElement): number {
  const rect = el.getBoundingClientRect();
  if (rect.width > 0) return rect.width;
  return el.clientWidth;
}

/**
 * Observe the real Reader workspace element with `ResizeObserver` and derive
 * {@link ReaderAskPresentationResult.effectiveSurface} from `requestedSurface`
 * plus the measured capacity. The requested surface is never mutated: an
 * explicit `floating` request always floats; a `sidecar` request docks only
 * when capacity is safe and otherwise falls back to floating, recovering
 * automatically when space is restored.
 *
 * Capacity responds to two independent signals:
 * 1. The workspace element's real width (via ResizeObserver).
 * 2. The viewport width, which drives the 29vw branch of the Ask column clamp
 *    and thus the required workspace width (via a window resize listener).
 *
 * The capacity basis is always the measured workspace element — never
 * `window.innerWidth`. React state is updated only when the derived capacity
 * boolean changes, so routine resizes that do not cross the threshold do not
 * trigger re-renders.
 */
export function useReaderAskPresentation(
  input: ReaderAskPresentationInput,
): ReaderAskPresentationResult {
  const { requestedSurface, workspaceEl, options } = input;

  // Resolve rem→px from the root element's computed font size, with a fallback.
  // Computed once per options.remPx change; does not track runtime root font
  // changes (acceptable for Phase 1).
  const remPx = useMemo(() => resolveRemPx(options?.remPx), [options?.remPx]);

  // Track viewport width so the 29vw Ask column clamp is reflected in the
  // capacity threshold. When the caller provides a fixed viewportWidthPx
  // (tests), use it directly; otherwise track via window resize.
  const viewportFromOptions = options?.viewportWidthPx;
  const [trackedViewportWidthPx, setTrackedViewportWidthPx] = useState(() =>
    resolveViewportWidthPx(viewportFromOptions),
  );
  const viewportWidthPx = viewportFromOptions ?? trackedViewportWidthPx;

  useEffect(() => {
    if (viewportFromOptions !== undefined) return;
    const update = () =>
      setTrackedViewportWidthPx((prev) => {
        const next = resolveViewportWidthPx();
        return next === prev ? prev : next;
      });
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [viewportFromOptions]);

  const requiredWorkspaceWidthPx = useMemo(
    () =>
      readerAskRequiredWorkspaceWidthPx(
        viewportWidthPx,
        READER_ASK_PRESENTATION_CONSTANTS,
        remPx,
      ),
    [viewportWidthPx, remPx],
  );

  const [hasSidecarCapacity, setHasSidecarCapacity] = useState<boolean>(false);
  // Track the last capacity decision to skip meaningless state updates.
  const capacityRef = useRef<boolean>(false);
  // Cache the last measured workspace width so a viewport-driven threshold
  // change can re-check capacity without waiting for a ResizeObserver fire.
  const workspaceWidthRef = useRef<number>(0);

  const recompute = useCallback(
    (width: number) => {
      workspaceWidthRef.current = width;
      const next = width >= requiredWorkspaceWidthPx;
      if (next !== capacityRef.current) {
        capacityRef.current = next;
        setHasSidecarCapacity(next);
      }
    },
    [requiredWorkspaceWidthPx],
  );

  useIsoLayoutEffect(() => {
    if (!workspaceEl) {
      workspaceWidthRef.current = 0;
      if (capacityRef.current !== false) {
        capacityRef.current = false;
        setHasSidecarCapacity(false);
      }
      return;
    }
    // Initial synchronous measurement before the observer fires, so the first
    // paint already reflects the real capacity decision.
    recompute(measureWorkspaceWidth(workspaceEl));
    if (typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(() => {
      recompute(measureWorkspaceWidth(workspaceEl));
    });
    observer.observe(workspaceEl);
    return () => {
      observer.disconnect();
    };
  }, [workspaceEl, recompute]);

  const askColumnWidthPx = useMemo(
    () =>
      readerAskColumnWidthPx(
        viewportWidthPx,
        READER_ASK_PRESENTATION_CONSTANTS,
        remPx,
      ),
    [viewportWidthPx, remPx],
  );

  const effectiveSurface: ReaderAskSurface =
    requestedSurface === "floating"
      ? "floating"
      : hasSidecarCapacity
        ? "sidecar"
        : "floating";

  return {
    effectiveSurface,
    hasSidecarCapacity,
    requiredWorkspaceWidthPx,
    askColumnWidthPx,
  };
}
