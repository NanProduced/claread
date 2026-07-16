"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/cn";
import {
  buildReaderRecordNavigationItems,
  type ReaderRecordNavigationItem,
} from "@/lib/reader-plate/projection/reader-record-navigation";
import type { ReaderRecordPlateDocument } from "@/lib/reader-plate/projection/reader-record-plate-document";
import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

const TOPBAR_SAFE_HEIGHT = 56; // px, sticky topbar + small gap
const SCROLL_LOCK_MS = 700;
const ACTIVE_SAFE_OFFSET = 8;
const PANEL_ANCHOR_EDGE_PADDING = 18;

/**
 * Locate the best scroll target for a reading unit inside the Reader Record
 * body. We never fall back to global `[data-unit-id]` because the rail itself
 * and other chrome elements may carry that attribute.
 *
 * Priority:
 * 1. paragraph with `data-reader-record-unit-start="true"` and matching unitId
 * 2. first paragraph with matching unitId
 */
function findUnitTarget(unitId: string): HTMLElement | null {
  const body = document.querySelector<HTMLElement>(
    ".reader-record-plate-document",
  );
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

function computeActiveUnitId(
  items: ReaderRecordNavigationItem[],
  targetMap: Map<string, HTMLElement>,
  safeTop: number,
): string | null {
  let lastAbove: string | null = null;
  let firstBelow: string | null = null;

  for (const item of items) {
    const target = targetMap.get(item.unitId);
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

  return lastAbove ?? firstBelow ?? items[0]?.unitId ?? null;
}

function clampPanelAnchorTop(top: number, railHeight: number): number {
  const lower = PANEL_ANCHOR_EDGE_PADDING;
  const upper = Math.max(lower, railHeight - PANEL_ANCHOR_EDGE_PADDING);
  return Math.min(Math.max(top, lower), upper);
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
  activeUnitId: string | null;
  focusedUnitId: string | null;
  panelOpen: boolean;
  panelAnchorTopPx: number | null;
  anchorMode?: "hover" | "center";
  className?: string;
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
  activeUnitId,
  focusedUnitId,
  panelOpen,
  anchorMode = "hover",
  panelAnchorTopPx,
  className,
  onItemClick,
  onItemKeyDown,
  onMouseEnter,
  onMouseLeave,
  registerRowRef,
}: NavigationPanelProps) {
  const shouldUseHoverAnchor =
    anchorMode === "hover" && panelAnchorTopPx !== null;
  const panelRef = useRef<HTMLDivElement>(null);
  const [clampedTop, setClampedTop] = useState<number | null>(null);

  useEffect(() => {
    if (!shouldUseHoverAnchor || panelAnchorTopPx === null) {
      setClampedTop(null);
      return;
    }
    const updateClamp = () => {
      const panel = panelRef.current;
      if (!panel) return;
      const height = panel.offsetHeight;
      const margin = 12; // safety margin

      let targetTop = panelAnchorTopPx;

      const wrapper = panel.parentElement;
      if (!wrapper) return;

      const wrapperHeight = wrapper.offsetHeight;
      if (wrapperHeight === 0 && height === 0) {
        setClampedTop(targetTop);
        return;
      }
      if (targetTop + height > wrapperHeight - margin) {
        targetTop = wrapperHeight - height - margin;
      }

      if (targetTop < margin) {
        targetTop = margin;
      }

      setClampedTop(targetTop);
    };

    updateClamp();

    if (typeof ResizeObserver !== "undefined" && panelRef.current) {
      const observer = new ResizeObserver(updateClamp);
      observer.observe(panelRef.current);
      return () => observer.disconnect();
    }
  }, [shouldUseHoverAnchor, panelAnchorTopPx]);

  return (
    <div
      ref={panelRef}
      data-testid="reader-record-navigation-panel"
      data-reader-record-navigation-panel="true"
      data-reader-record-navigation-panel-anchor-y={
        shouldUseHoverAnchor ? String(Math.round(panelAnchorTopPx!)) : undefined
      }
      className={cn(
        "reader-record-navigation-panel motion-reduce:transition-none",
        "transition-[transform,opacity,visibility] duration-200 ease-[var(--cl-ease-standard)]",
        "absolute z-10 max-h-[min(72vh,42rem)] origin-right",
        shouldUseHoverAnchor
          ? "right-[calc(100%+8px)]"
          : "right-0 top-1/2 -translate-y-1/2",
        panelOpen
          ? "visible translate-x-0 opacity-100"
          : "invisible translate-x-2 scale-[0.98] opacity-0 pointer-events-none",
        className,
      )}
      style={
        shouldUseHoverAnchor && clampedTop !== null
          ? { top: `${clampedTop}px` }
          : undefined
      }
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
        <div className="max-h-[min(72vh,42rem)] overflow-y-auto py-2">
          <ol className="flex flex-col">
            {items.map((item) => (
              <NavigationPanelRow
                key={item.unitId}
                item={item}
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
  const items = useMemo(
    () => buildReaderRecordNavigationItems(snapshot, plateDocument),
    [snapshot, plateDocument],
  );

  const [activeUnitId, setActiveUnitId] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelAnchorTopPx, setPanelAnchorTopPx] = useState<number | null>(null);
  // Roving tabindex: tracks which row currently holds tabIndex=0.
  const [focusedUnitId, setFocusedUnitId] = useState<string | null>(null);

  const wrapperRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const rowRefsRef = useRef<Map<string, HTMLButtonElement>>(new Map());
  const closeTimerRef = useRef<number | null>(null);
  const scrollLockTimerRef = useRef<number | null>(null);
  const lockedUnitIdRef = useRef<string | null>(null);
  const targetMapRef = useRef<Map<string, HTMLElement>>(new Map());

  // --- Target map caching (performance) ---------------------------------
  // Rebuild the target map only when items change, not on every scroll frame.
  // Missing targets are lazily populated on scroll until the map is complete.
  const refreshTargetsLazy = useCallback((): Map<string, HTMLElement> => {
    const map = targetMapRef.current;
    if (map.size >= items.length && items.length > 0) return map;
    for (const item of items) {
      if (map.has(item.unitId)) continue;
      const target = findUnitTarget(item.unitId);
      if (target) {
        map.set(item.unitId, target);
      }
    }
    return map;
  }, [items]);

  // Invalidate cache when items change (different unit ids or count).
  useEffect(() => {
    targetMapRef.current = new Map();
  }, [items]);

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

  const setPanelAnchorFromElement = useCallback((element: Element) => {
    const wrapper = wrapperRef.current;
    if (!wrapper) {
      return;
    }
    const wrapperRect = wrapper.getBoundingClientRect();
    const elementRect = element.getBoundingClientRect();
    setPanelAnchorTopPx(
      clampPanelAnchorTop(
        elementRect.top + elementRect.height / 2 - wrapperRect.top,
        wrapperRect.height,
      ),
    );
  }, []);

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

  // --- Scroll-based active section (cached target map) ------------------
  useEffect(() => {
    if (typeof window === "undefined" || items.length === 0) return;

    const scrollContainer = getScrollContainer() ?? window;
    let rafId: number | null = null;
    let pending = false;

    const updateActive = () => {
      if (pending) return;
      pending = true;
      rafId = window.requestAnimationFrame(() => {
        pending = false;
        if (lockedUnitIdRef.current) return;

        // Use cached map — no querySelectorAll per frame.
        const map = refreshTargetsLazy();
        const activeId = computeActiveUnitId(
          items,
          map,
          TOPBAR_SAFE_HEIGHT + ACTIVE_SAFE_OFFSET,
        );
        if (activeId) {
          setActiveUnitId(activeId);
        }
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
  }, [items, refreshTargetsLazy]);

  // Default the active item to the first unit until scrolling provides a signal.
  useEffect(() => {
    if (items.length > 0 && activeUnitId === null) {
      setActiveUnitId(items[0].unitId);
    }
  }, [items, activeUnitId]);

  // Initialize focusedUnitId to the active item when the panel opens.
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
      const target = targetMapRef.current.get(unitId) ?? findUnitTarget(unitId);
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
    (event: React.MouseEvent<HTMLSpanElement>) => {
      setPanelAnchorFromElement(event.currentTarget);
      openPanel();
    },
    [openPanel, setPanelAnchorFromElement],
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

  if (items.length === 0) {
    return null;
  }

  const isCanvas = layout === "canvas";
  const activeLabel =
    items.find((item) => item.unitId === activeUnitId)?.label ??
    items[0]?.label ??
    "";
  const activeIndex =
    items.findIndex((item) => item.unitId === activeUnitId) + 1;
  const triggerLabel = panelOpen
    ? `关闭段落导航，当前第 ${activeIndex} 段`
    : `打开段落导航，当前第 ${activeIndex} 段`;

  return (
    <nav
      ref={wrapperRef}
      aria-label="阅读定位"
      data-testid="reader-record-navigation-rail"
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
        activeUnitId={activeUnitId}
        focusedUnitId={focusedUnitId}
        panelOpen={panelOpen}
        panelAnchorTopPx={panelAnchorTopPx}
        anchorMode="hover"
        onItemClick={handleItemClick}
        onItemKeyDown={handleItemKeyDown}
        onMouseEnter={keepOpenPanel}
        onMouseLeave={handleMouseLeave}
        registerRowRef={registerRowRef}
      />

      {/* Accessible trigger button wrapping the visual ticks.
          The ticks are aria-hidden; the button's aria-label is the sole
          screen-reader entry point. Mouse hover on individual ticks still
          anchors and opens the panel. */}
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
  active: boolean;
  tabIndex?: number;
  onClick: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void;
  registerRef: (el: HTMLButtonElement | null) => void;
}

function NavigationPanelRow({
  item,
  active,
  tabIndex = -1,
  onClick,
  onKeyDown,
  registerRef,
}: NavigationPanelRowProps) {
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
          "focus-visible:outline-none focus-visible:bg-ink/[0.04]",
          active
            ? "bg-ink/[0.055] font-medium text-ink"
            : "text-ink/60 hover:bg-ink/[0.035] hover:text-ink",
        )}
      >
        <span className="block truncate text-[11px] leading-snug">
          {item.label}
        </span>
        <span className="block text-[9px] leading-snug text-muted-foreground/75">
          第 {item.fallbackIndex + 1} 段
        </span>
      </button>
    </li>
  );
}
