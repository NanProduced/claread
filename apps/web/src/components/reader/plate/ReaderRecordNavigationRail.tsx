"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/cn";
import {
  projectReaderRecordNavigation,
  type ReaderRecordNavigationItem,
  type ReaderRecordNavigationMode,
} from "@/lib/reader-plate/projection/reader-record-navigation";
import type { ReaderRecordPlateDocument } from "@/lib/reader-plate/projection/reader-record-plate-document";
import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

const TOPBAR_SAFE_HEIGHT = 56; // px, sticky topbar + small gap
const SCROLL_LOCK_MS = 700;
const ACTIVE_SAFE_OFFSET = 8;

const PLATE_DOCUMENT_SELECTOR = ".reader-record-plate-document";

function getPlateDocumentRoot(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.querySelector<HTMLElement>(PLATE_DOCUMENT_SELECTOR);
}

/**
 * Locate the best scroll target for a reading unit inside the Reader Record
 * body. We never fall back to global `[data-unit-id]` because the rail itself
 * and other chrome elements may carry that attribute.
 *
 * Priority:
 * 1. paragraph with `data-reader-record-unit-start="true"` and matching unitId
 * 2. first paragraph with matching unitId
 */
function findUnitTarget(
  unitId: string,
  plateRoot: HTMLElement | null = getPlateDocumentRoot(),
): HTMLElement | null {
  const body = plateRoot;
  if (!body) return null;

  const paragraphs = body.querySelectorAll<HTMLElement>(
    '[data-reader-record-node="paragraph"]',
  );

  let fallback: HTMLElement | null = null;
  for (const paragraph of paragraphs) {
    if (paragraph.getAttribute("data-unit-id") !== unitId) continue;
    if (paragraph.getAttribute("data-reader-record-unit-start") === "true") {
      return paragraph;
    }
    if (fallback === null) {
      fallback = paragraph;
    }
  }

  return fallback;
}

/**
 * Cache entry is valid only when it is still a live paragraph for `unitId`
 * under the current plate document root. Detached or remounted nodes (common
 * after Plate setValue) must not drive scroll spy or click positioning.
 */
function isValidCachedUnitTarget(
  unitId: string,
  el: HTMLElement,
  plateRoot: HTMLElement | null,
): boolean {
  if (!el.isConnected) return false;
  if (!plateRoot || !plateRoot.contains(el)) return false;
  if (el.getAttribute("data-reader-record-node") !== "paragraph") return false;
  if (el.getAttribute("data-unit-id") !== unitId) return false;
  return true;
}

/**
 * Single validated target resolver for scroll spy and click.
 * Invalid cache entries are deleted and re-resolved immediately.
 */
function resolveValidatedUnitTarget(
  unitId: string,
  map: Map<string, HTMLElement>,
  plateRoot: HTMLElement | null = getPlateDocumentRoot(),
): HTMLElement | null {
  const cached = map.get(unitId);
  if (cached) {
    if (isValidCachedUnitTarget(unitId, cached, plateRoot)) {
      return cached;
    }
    map.delete(unitId);
  }

  const resolved = findUnitTarget(unitId, plateRoot);
  if (resolved && isValidCachedUnitTarget(unitId, resolved, plateRoot)) {
    map.set(unitId, resolved);
    return resolved;
  }
  return null;
}

/**
 * Active unit for scroll spy.
 * - L0: last unit above safeTop, else first below, else first item.
 * - L1: only last heading above safeTop; lead zone (all headings below) → null.
 *   Body coverage keeps the previous heading active because body is not a candidate.
 */
function computeActiveUnitId(
  items: ReaderRecordNavigationItem[],
  targetMap: Map<string, HTMLElement>,
  safeTop: number,
  mode: ReaderRecordNavigationMode,
): string | null {
  // Query the plate root once per frame. Resolution remains lazy for only
  // the ordered candidates actually examined by the scroll spy.
  const plateRoot = getPlateDocumentRoot();
  let lastAbove: string | null = null;
  let firstBelow: string | null = null;

  for (const item of items) {
    // Prefer validated resolver so detached cache entries cannot drive active.
    const target = resolveValidatedUnitTarget(
      item.unitId,
      targetMap,
      plateRoot,
    );
    if (!target) continue;

    const top = target.getBoundingClientRect().top;
    if (top <= safeTop) {
      lastAbove = item.unitId;
    } else if (firstBelow === null) {
      firstBelow = item.unitId;
      // Items are ordered; the first one below the safe line is enough.
      break;
    }
  }

  if (mode === "L1") {
    // Lead zone: no heading has been crossed — do not pretend first heading is active.
    return lastAbove;
  }

  return lastAbove ?? firstBelow ?? items[0]?.unitId ?? null;
}

function buildNavigationTriggerLabel(
  mode: ReaderRecordNavigationMode,
  panelOpen: boolean,
  activeIndex: number | null,
): string {
  if (mode === "L1") {
    const action = panelOpen ? "关闭章节导航" : "打开章节导航";
    if (activeIndex === null) {
      return action;
    }
    return `${action}，当前第 ${activeIndex} 项`;
  }

  const action = panelOpen ? "关闭段落导航" : "打开段落导航";
  // L0 always has a current segment index once items exist.
  return `${action}，当前第 ${activeIndex ?? 1} 段`;
}

/**
 * Find the real scroll container for the Reader Record body. The app shell
 * wraps content in a Radix ScrollArea, so `window` is not always the element
 * that scrolls. We walk up from the plate document until we find an element
 * with overflow auto/scroll, falling back to `window`.
 */
function getScrollContainer(): Window | HTMLElement | null {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return null;
  }

  const body = document.querySelector<HTMLElement>(
    ".reader-record-plate-document",
  );
  if (!body) return window;

  let el: HTMLElement | null = body.parentElement;
  while (el && el !== document.body && el !== document.documentElement) {
    const style = window.getComputedStyle(el);
    if (/(auto|scroll)/.test(style.overflowY + style.overflow)) {
      return el;
    }
    el = el.parentElement;
  }

  return window;
}

// ---------------------------------------------------------------------------
// Visual ticks — purely decorative, aria-hidden. Mouse hover anchors the panel.
// ---------------------------------------------------------------------------

interface VisualTicksProps {
  items: ReaderRecordNavigationItem[];
  activeUnitId: string | null;
  onTickMouseEnter: (event: React.MouseEvent<HTMLSpanElement>) => void;
}

function VisualTicks({
  items,
  activeUnitId,
  onTickMouseEnter,
}: VisualTicksProps) {
  return (
    <span
      data-testid="reader-record-mini-rail"
      className="reader-record-mini-rail flex h-full w-full flex-col items-end justify-center gap-[2px] overflow-hidden py-4"
      aria-hidden="true"
    >
      {items.map((item) => (
        <span
          key={item.unitId}
          className="group relative flex min-h-[7px] w-10 flex-1 max-h-4 shrink items-center justify-end rounded-sm px-1"
          data-navigation-unit-id={item.unitId}
          onMouseEnter={onTickMouseEnter}
        >
          <span
            className={cn(
              "block h-[1.5px] rounded-full transition-all duration-150 ease-[var(--cl-ease-standard)]",
              item.unitId === activeUnitId
                ? "w-5 bg-ink/60"
                : "w-3.5 bg-ink/18 group-hover:bg-ink/40",
            )}
          />
        </span>
      ))}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Navigation panel — the main interactive list with roving tabindex.
// ---------------------------------------------------------------------------

interface NavigationPanelProps {
  items: ReaderRecordNavigationItem[];
  mode: ReaderRecordNavigationMode;
  activeUnitId: string | null;
  focusedUnitId: string | null;
  panelOpen: boolean;
  className?: string;
  getRowRef: (unitId: string) => HTMLButtonElement | null;
  onItemClick: (unitId: string) => void;
  onItemKeyDown: (
    event: React.KeyboardEvent<HTMLButtonElement>,
    unitId: string,
  ) => void;
  onMouseEnter: () => void;
  onMouseLeave: (event: React.MouseEvent<HTMLElement>) => void;
  registerRowRef: (unitId: string, el: HTMLButtonElement | null) => void;
}

function NavigationPanel({
  items,
  mode,
  activeUnitId,
  focusedUnitId,
  panelOpen,
  className,
  getRowRef,
  onItemClick,
  onItemKeyDown,
  onMouseEnter,
  onMouseLeave,
  registerRowRef,
}: NavigationPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);

  // Keep the active or keyboard-focused row visible inside the panel's own
  // scrollport. Focused row takes precedence while roving; otherwise the
  // scroll-spy active row is kept in view.
  useEffect(() => {
    if (!panelOpen) return;
    const row = (focusedUnitId ? getRowRef(focusedUnitId) : null) ??
      (activeUnitId ? getRowRef(activeUnitId) : null);
    const scrollArea = scrollAreaRef.current;
    if (row && scrollArea && scrollArea.contains(row) && typeof row.scrollIntoView === "function") {
      row.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }, [panelOpen, activeUnitId, focusedUnitId, getRowRef]);

  return (
    <div
      ref={panelRef}
      data-testid="reader-record-navigation-panel"
      data-reader-record-navigation-panel="true"
      className={cn(
        "reader-record-navigation-panel motion-reduce:transition-none",
        "transition-[transform,opacity,visibility] duration-200 ease-[var(--cl-ease-standard)]",
        "absolute right-[calc(100%+8px)] top-1/2 z-10 max-h-[min(72vh,42rem)] origin-right -translate-y-1/2",
        panelOpen
          ? "visible translate-x-0 opacity-100"
          : "invisible translate-x-2 scale-[0.98] opacity-0 pointer-events-none",
        className,
      )}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      aria-hidden={!panelOpen}
    >
      <div
        className={cn(
          "flex max-h-[min(72vh,42rem)] flex-col overflow-hidden rounded-lg border border-hairline/50",
          "bg-surface-raised/95 shadow-[var(--app-panel-shadow-quiet)] backdrop-blur-sm",
          "w-64",
        )}
      >
        <div
          ref={scrollAreaRef}
          className="max-h-[min(72vh,42rem)] overflow-y-auto py-2"
        >
          <ol className="flex flex-col">
            {items.map((item) => (
              <NavigationPanelRow
                key={item.unitId}
                item={item}
                mode={mode}
                active={item.unitId === activeUnitId}
                // Roving tabindex: only the focused item is tabbable (0),
                // all others are -1. When closed, all are -1.
                tabIndex={
                  panelOpen && item.unitId === focusedUnitId ? 0 : -1
                }
                onClick={() => onItemClick(item.unitId)}
                onKeyDown={(event) => onItemKeyDown(event, item.unitId)}
                registerRef={(el) => registerRowRef(item.unitId, el)}
              />
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

export interface ReaderRecordNavigationRailProps {
  snapshot: ReaderPlateSnapshotDto;
  plateDocument: ReaderRecordPlateDocument;
  askOpen?: boolean;
  className?: string;
  /**
   * `viewport` keeps the rail fixed to the viewport right edge (legacy).
   * `canvas` fills the Reader Canvas outline slot; the slot owns responsive
   * visibility and viewport pinning inside ReaderRecordPlateSurface.
   */
  layout?: "viewport" | "canvas";
}

export function ReaderRecordNavigationRail({
  snapshot,
  plateDocument,
  askOpen = false,
  className,
  layout = "viewport",
}: ReaderRecordNavigationRailProps) {
  const projection = useMemo(
    () => projectReaderRecordNavigation(snapshot, plateDocument),
    [snapshot, plateDocument],
  );
  const { mode, items, sourceIdentityKey } = projection;

  const [activeUnitId, setActiveUnitId] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  // Roving tabindex: tracks which row currently holds tabIndex=0.
  const [focusedUnitId, setFocusedUnitId] = useState<string | null>(null);

  const wrapperRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const rowRefsRef = useRef<Map<string, HTMLButtonElement>>(new Map());
  const closeTimerRef = useRef<number | null>(null);
  const scrollLockTimerRef = useRef<number | null>(null);
  const lockedUnitIdRef = useRef<string | null>(null);
  const targetMapRef = useRef<Map<string, HTMLElement>>(new Map());
  const sourceIdentityKeyRef = useRef(sourceIdentityKey);

  // Invalidate cache when items change (different unit ids or count).
  useEffect(() => {
    targetMapRef.current = new Map();
  }, [items]);

  // Source identity reset: base_id:generation change must clear all rail state,
  // even when unit ids still look like u1/u2.
  useEffect(() => {
    if (sourceIdentityKeyRef.current === sourceIdentityKey) {
      return;
    }
    sourceIdentityKeyRef.current = sourceIdentityKey;
    setActiveUnitId(null);
    setFocusedUnitId(null);
    lockedUnitIdRef.current = null;
    if (scrollLockTimerRef.current !== null) {
      window.clearTimeout(scrollLockTimerRef.current);
      scrollLockTimerRef.current = null;
    }
    targetMapRef.current = new Map();
  }, [sourceIdentityKey]);

  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const openPanel = useCallback(() => {
    clearCloseTimer();
    setPanelOpen(true);
  }, [clearCloseTimer]);

  const closePanel = useCallback(() => {
    clearCloseTimer();
    setPanelOpen(false);
  }, [clearCloseTimer]);

  const keepOpenPanel = useCallback(() => {
    if (!panelOpen) return;
    openPanel();
  }, [openPanel, panelOpen]);

  const scheduleClose = useCallback(() => {
    clearCloseTimer();
    closeTimerRef.current = window.setTimeout(() => {
      setPanelOpen(false);
    }, 220);
  }, [clearCloseTimer]);

  const lockActiveUnit = useCallback((unitId: string) => {
    lockedUnitIdRef.current = unitId;
    if (scrollLockTimerRef.current !== null) {
      window.clearTimeout(scrollLockTimerRef.current);
    }
    scrollLockTimerRef.current = window.setTimeout(() => {
      lockedUnitIdRef.current = null;
    }, SCROLL_LOCK_MS);
  }, []);

  // --- Scroll-based active section (validated target map) ---------------
  useEffect(() => {
    if (typeof window === "undefined" || items.length === 0) return;

    // Fence: rAF scheduled for this source must not commit after base/generation switches.
    const fenceSourceIdentityKey = sourceIdentityKey;
    const scrollContainer = getScrollContainer() ?? window;
    let rafId: number | null = null;
    let pending = false;

    const updateActive = () => {
      if (pending) return;
      pending = true;
      rafId = window.requestAnimationFrame(() => {
        pending = false;
        // Drop stale frames from a previous source identity.
        if (sourceIdentityKeyRef.current !== fenceSourceIdentityKey) {
          return;
        }
        if (lockedUnitIdRef.current) return;

        const activeId = computeActiveUnitId(
          items,
          targetMapRef.current,
          TOPBAR_SAFE_HEIGHT + ACTIVE_SAFE_OFFSET,
          mode,
        );
        // L1 lead zone yields null; must clear any previous active.
        setActiveUnitId(activeId);
      });
    };

    scrollContainer.addEventListener("scroll", updateActive, { passive: true });
    window.addEventListener("resize", updateActive);
    updateActive();

    return () => {
      scrollContainer.removeEventListener("scroll", updateActive);
      window.removeEventListener("resize", updateActive);
      if (rafId !== null) {
        window.cancelAnimationFrame(rafId);
      }
    };
  }, [items, mode, sourceIdentityKey]);

  // L0 only: default the active item to the first unit until scrolling provides a signal.
  // L1 lead zone must keep activeUnitId = null (no first-heading pseudo-active).
  useEffect(() => {
    if (mode !== "L0") return;
    if (items.length > 0 && activeUnitId === null) {
      setActiveUnitId(items[0].unitId);
    }
  }, [mode, items, activeUnitId]);

  // Initialize focusedUnitId when the panel opens.
  // L1 lead: active may be null, but keyboard focus can still land on first heading.
  useEffect(() => {
    if (panelOpen && focusedUnitId === null) {
      setFocusedUnitId(activeUnitId ?? items[0]?.unitId ?? null);
    }
    if (!panelOpen) {
      setFocusedUnitId(null);
    }
  }, [panelOpen, activeUnitId, focusedUnitId, items]);

  // Focus the row matching focusedUnitId when it changes (keyboard nav).
  useEffect(() => {
    if (!panelOpen || focusedUnitId === null) return;
    const row = rowRefsRef.current.get(focusedUnitId);
    row?.focus();
  }, [focusedUnitId, panelOpen]);

  // Clean up lingering timers if the rail is unmounted.
  useEffect(() => {
    return () => {
      if (closeTimerRef.current !== null) {
        window.clearTimeout(closeTimerRef.current);
      }
      if (scrollLockTimerRef.current !== null) {
        window.clearTimeout(scrollLockTimerRef.current);
      }
    };
  }, []);

  // --- Navigation actions -----------------------------------------------
  const handleItemClick = useCallback(
    (unitId: string) => {
      // Same validated resolver as scroll spy — never scroll to a detached node.
      const target = resolveValidatedUnitTarget(unitId, targetMapRef.current);
      if (target) {
        const scrollContainer = getScrollContainer() ?? window;
        const isWindow = scrollContainer === window;
        const scrollTop = isWindow
          ? window.scrollY
          : (scrollContainer as HTMLElement).scrollTop;
        const containerTop = isWindow
          ? 0
          : (scrollContainer as HTMLElement).getBoundingClientRect().top;
        const targetRect = target.getBoundingClientRect();
        const targetOffset = targetRect.top - containerTop + scrollTop;
        const safeOffset = Math.max(
          0,
          TOPBAR_SAFE_HEIGHT + ACTIVE_SAFE_OFFSET - containerTop,
        );
        const top = targetOffset - safeOffset;

        if (isWindow) {
          window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
        } else {
          (scrollContainer as HTMLElement).scrollTo({
            top: Math.max(0, top),
            behavior: "smooth",
          });
        }
      }
      setActiveUnitId(unitId);
      setFocusedUnitId(unitId);
      lockActiveUnit(unitId);
    },
    [lockActiveUnit],
  );

  // --- Keyboard navigation (roving tabindex) ----------------------------
  const focusRow = useCallback(
    (unitId: string) => {
      setFocusedUnitId(unitId);
    },
    [],
  );

  const handleItemKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>, unitId: string) => {
      const currentIndex = items.findIndex((item) => item.unitId === unitId);
      if (currentIndex === -1) return;

      switch (event.key) {
        case "Enter":
        case " ": {
          event.preventDefault();
          handleItemClick(unitId);
          break;
        }
        case "ArrowDown": {
          event.preventDefault();
          const nextIndex = Math.min(items.length - 1, currentIndex + 1);
          focusRow(items[nextIndex].unitId);
          break;
        }
        case "ArrowUp": {
          event.preventDefault();
          const prevIndex = Math.max(0, currentIndex - 1);
          focusRow(items[prevIndex].unitId);
          break;
        }
        case "Home": {
          event.preventDefault();
          focusRow(items[0].unitId);
          break;
        }
        case "End": {
          event.preventDefault();
          focusRow(items[items.length - 1].unitId);
          break;
        }
        case "Escape": {
          event.preventDefault();
          closePanel();
          // Return focus to the outline trigger button.
          triggerRef.current?.focus();
          break;
        }
      }
    },
    [items, handleItemClick, focusRow, closePanel],
  );

  const handleTriggerKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (panelOpen) {
          closePanel();
        } else {
          openPanel();
        }
      }
    },
    [panelOpen, openPanel, closePanel],
  );

  const handleTriggerClick = useCallback(() => {
    if (panelOpen) {
      closePanel();
    } else {
      openPanel();
    }
  }, [panelOpen, openPanel, closePanel]);

  const handleBlur = useCallback((event: React.FocusEvent<HTMLElement>) => {
    if (!wrapperRef.current?.contains(event.relatedTarget as Node)) {
      setPanelOpen(false);
    }
  }, []);

  const handleMouseLeave = useCallback(
    (event: React.MouseEvent<HTMLElement>) => {
      const related = event.relatedTarget;
      if (
        related instanceof Element &&
        wrapperRef.current?.contains(related as Node)
      ) {
        return;
      }
      scheduleClose();
    },
    [scheduleClose],
  );

  const handleTickMouseEnter = useCallback(
    () => {
      // T5.1e-PUX-Rail-R1: panel has a single stable vertical position; hover
      // only opens it, it no longer re-anchors to the tick's y-coordinate.
      openPanel();
    },
    [openPanel],
  );

  const registerRowRef = useCallback(
    (unitId: string, el: HTMLButtonElement | null) => {
      if (el) {
        rowRefsRef.current.set(unitId, el);
      } else {
        rowRefsRef.current.delete(unitId);
      }
    },
    [],
  );

  const getRowRef = useCallback(
    (unitId: string) => rowRefsRef.current.get(unitId) ?? null,
    [],
  );

  if (items.length === 0) {
    return null;
  }

  const isCanvas = layout === "canvas";
  const activeItemIndex =
    activeUnitId === null
      ? -1
      : items.findIndex((item) => item.unitId === activeUnitId);
  const activeIndexForLabel =
    activeItemIndex >= 0 ? activeItemIndex + 1 : null;
  const triggerLabel = buildNavigationTriggerLabel(
    mode,
    panelOpen,
    activeIndexForLabel,
  );

  return (
    <nav
      ref={wrapperRef}
      aria-label="阅读定位"
      data-testid="reader-record-navigation-rail"
      data-navigation-mode={mode}
      data-layout={layout}
      className={cn(
        "hidden md:flex",
        // When the detail panel is expanded, elevate the rail above the
        // floating Ask window so the outline remains reachable. Semantic
        // z-index: rail (30) < floating Ask (40) < expanded panel (50).
        panelOpen
          ? "z-[var(--reader-z-outline-panel-expanded,50)]"
          : "z-[var(--reader-z-outline-rail,30)]",
        isCanvas
          ? "reader-record-navigation-rail--canvas absolute right-0 top-1/2 h-[min(72vh,42rem)] w-full -translate-y-1/2"
          : "fixed right-3 top-1/2 h-[min(72vh,42rem)] -translate-y-1/2",
        // Viewport-mode legacy shift when Ask is open. Canvas mode relies on
        // the Reader Canvas CSS variables instead of this clamp.
        !isCanvas &&
          askOpen &&
          "2xl:right-[clamp(31.75rem,calc((100vw-124px-96ch)/2+0.25rem),38.25rem)]",
        className,
      )}
      onMouseLeave={handleMouseLeave}
      onBlur={handleBlur}
    >
      {/* Detail panel: rendered first so it sits to the left of the ticks. */}
      <NavigationPanel
        items={items}
        mode={mode}
        activeUnitId={activeUnitId}
        focusedUnitId={focusedUnitId}
        panelOpen={panelOpen}
        getRowRef={getRowRef}
        onItemClick={handleItemClick}
        onItemKeyDown={handleItemKeyDown}
        onMouseEnter={keepOpenPanel}
        onMouseLeave={handleMouseLeave}
        registerRowRef={registerRowRef}
      />

      {/* Accessible trigger button wrapping the visual ticks.
          The ticks are aria-hidden; the button's aria-label is the sole
          screen-reader entry point. Mouse hover on individual ticks opens
          the panel, but the panel stays at one stable vertical position. */}
      <button
        ref={triggerRef}
        type="button"
        data-testid="reader-record-outline-trigger"
        data-reader-record-outline-trigger="true"
        className="relative flex min-h-[24px] min-w-[24px] cursor-pointer items-center justify-end"
        aria-label={triggerLabel}
        aria-expanded={panelOpen}
        aria-haspopup="menu"
        onClick={handleTriggerClick}
        onKeyDown={handleTriggerKeyDown}
      >
        <VisualTicks
          items={items}
          activeUnitId={activeUnitId}
          onTickMouseEnter={handleTickMouseEnter}
        />
      </button>
    </nav>
  );
}

interface NavigationPanelRowProps {
  item: ReaderRecordNavigationItem;
  mode: ReaderRecordNavigationMode;
  active: boolean;
  tabIndex?: number;
  onClick: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void;
  registerRef: (el: HTMLButtonElement | null) => void;
}

function NavigationPanelRow({
  item,
  mode,
  active,
  tabIndex = -1,
  onClick,
  onKeyDown,
  registerRef,
}: NavigationPanelRowProps) {
  const indexLabel =
    mode === "L1"
      ? `第 ${item.fallbackIndex + 1} 项`
      : `第 ${item.fallbackIndex + 1} 段`;

  return (
    <li>
      <button
        ref={registerRef}
        type="button"
        aria-current={active ? "true" : undefined}
        tabIndex={tabIndex}
        onClick={onClick}
        onKeyDown={onKeyDown}
        className={cn(
          "relative w-full px-2.5 py-1.5 text-left transition-colors duration-150 ease-[var(--cl-ease-standard)]",
          "focus-visible:outline-none focus-visible:bg-ink/[0.035] focus-visible:ring-1 focus-visible:ring-lens-blue/30",
          active
            ? "bg-[var(--app-control-current)] font-medium text-ink"
            : "text-ink/60 hover:bg-ink/[0.035] hover:text-ink",
        )}
      >
        <span className="block truncate text-[11px] leading-snug">
          {item.label}
        </span>
        <span className="block text-[9px] leading-snug text-muted-foreground/75">
          {indexLabel}
        </span>
      </button>
    </li>
  );
}
