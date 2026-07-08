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

interface MiniRailTicksProps {
  items: ReaderRecordNavigationItem[];
  activeUnitId: string | null;
  panelOpen: boolean;
  className?: string;
  onItemClick: (unitId: string) => void;
  onItemKeyDown: (
    event: React.KeyboardEvent<HTMLButtonElement>,
    unitId: string,
  ) => void;
  onMouseEnter: (event: React.MouseEvent<HTMLDivElement>) => void;
  onMouseMove: (event: React.MouseEvent<HTMLDivElement>) => void;
  onFocusCapture: (event: React.FocusEvent<HTMLDivElement>) => void;
}

function MiniRailTicks({
  items,
  activeUnitId,
  panelOpen,
  className,
  onItemClick,
  onItemKeyDown,
  onMouseEnter,
  onMouseMove,
  onFocusCapture,
}: MiniRailTicksProps) {
  return (
    <div
      data-testid="reader-record-mini-rail"
      className={cn(
        "reader-record-mini-rail relative flex h-full w-full flex-col items-end gap-[2px] overflow-hidden py-4",
        className,
      )}
      onMouseEnter={onMouseEnter}
      onMouseMove={onMouseMove}
      onFocusCapture={onFocusCapture}
    >
      {items.map((item) => (
        <button
          key={item.unitId}
          type="button"
          aria-label={item.label}
          aria-current={item.unitId === activeUnitId ? "true" : undefined}
          tabIndex={0}
          onClick={() => onItemClick(item.unitId)}
          onKeyDown={(event) => onItemKeyDown(event, item.unitId)}
          className="group relative flex min-h-[7px] w-10 flex-1 max-h-4 shrink items-center justify-end rounded-sm px-1"
          data-navigation-unit-id={item.unitId}
        >
          <span
            className={cn(
              "block h-[1.5px] rounded-full transition-all duration-150 ease-[var(--cl-ease-standard)]",
              panelOpen && "opacity-0",
              item.unitId === activeUnitId
                ? "w-5 bg-ink/60"
                : "w-3.5 bg-ink/18 group-hover:bg-ink/40",
            )}
          />
        </button>
      ))}
    </div>
  );
}

interface NavigationPanelProps {
  items: ReaderRecordNavigationItem[];
  activeUnitId: string | null;
  panelOpen: boolean;
  panelAnchorTopPx: number | null;
  className?: string;
  onItemClick: (unitId: string) => void;
  onItemKeyDown: (
    event: React.KeyboardEvent<HTMLButtonElement>,
    unitId: string,
  ) => void;
  onMouseEnter: () => void;
  onMouseLeave: (event: React.MouseEvent<HTMLElement>) => void;
}

function NavigationPanel({
  items,
  activeUnitId,
  panelOpen,
  panelAnchorTopPx,
  className,
  onItemClick,
  onItemKeyDown,
  onMouseEnter,
  onMouseLeave,
}: NavigationPanelProps) {
  return (
    <div
      data-testid="reader-record-navigation-panel"
      data-reader-record-navigation-panel="true"
      data-reader-record-navigation-panel-anchor-y={
        panelAnchorTopPx !== null ? String(Math.round(panelAnchorTopPx)) : undefined
      }
      className={cn(
        "reader-record-navigation-panel motion-reduce:transition-none",
        "transition-[transform,opacity,visibility] duration-200 ease-[var(--cl-ease-standard)]",
        "absolute right-0 top-1/2 z-10 max-h-[min(72vh,42rem)] -translate-y-1/2 origin-right",
        panelOpen
          ? "visible translate-x-0 opacity-100"
          : "invisible translate-x-2 scale-[0.98] opacity-0 pointer-events-none",
        className,
      )}
      style={panelAnchorTopPx !== null ? { top: `${panelAnchorTopPx}px` } : undefined}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      aria-hidden={!panelOpen}
    >
      <div
        className={cn(
          "flex max-h-[min(72vh,42rem)] flex-col overflow-hidden rounded-lg border border-hairline/50",
          "bg-[var(--surface-raised)]/95 shadow-[0_10px_24px_rgba(23,21,17,0.08)] backdrop-blur-sm",
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
                tabIndex={panelOpen ? 0 : -1}
                onClick={() => onItemClick(item.unitId)}
                onKeyDown={(event) => onItemKeyDown(event, item.unitId)}
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
   * `canvas` anchors the rail inside the Reader Canvas Grid outline slot,
   * which is the default inside ReaderRecordPlateSurface.
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
  const wrapperRef = useRef<HTMLElement>(null);
  const closeTimerRef = useRef<number | null>(null);
  const scrollLockTimerRef = useRef<number | null>(null);
  const lockedUnitIdRef = useRef<string | null>(null);
  const targetMapRef = useRef<Map<string, HTMLElement>>(new Map());

  const refreshTargets = useCallback(() => {
    const map = new Map<string, HTMLElement>();
    for (const item of items) {
      const target = findUnitTarget(item.unitId);
      if (target) {
        map.set(item.unitId, target);
      }
    }
    targetMapRef.current = map;
    return map;
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

  const setPanelAnchorFromClientY = useCallback((clientY: number) => {
    if (!Number.isFinite(clientY)) {
      return;
    }
    const wrapper = wrapperRef.current;
    if (!wrapper) {
      return;
    }
    const rect = wrapper.getBoundingClientRect();
    setPanelAnchorTopPx(
      clampPanelAnchorTop(clientY - rect.top, rect.height),
    );
  }, []);

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

  // Deterministic active unit computation on scroll/resize.
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

        const map = refreshTargets();
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
  }, [items, refreshTargets]);

  // Default the active item to the first unit until scrolling provides a signal.
  useEffect(() => {
    if (items.length > 0 && activeUnitId === null) {
      setActiveUnitId(items[0].unitId);
    }
  }, [items, activeUnitId]);

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

  const handleItemClick = useCallback(
    (unitId: string) => {
      const target = findUnitTarget(unitId);
      if (!target) return;

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
      setActiveUnitId(unitId);
      lockActiveUnit(unitId);
      openPanel();
    },
    [lockActiveUnit, openPanel],
  );

  const handleItemKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>, unitId: string) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        handleItemClick(unitId);
      }
    },
    [handleItemClick],
  );

  const handleBlur = useCallback(
    (event: React.FocusEvent<HTMLElement>) => {
      if (!wrapperRef.current?.contains(event.relatedTarget as Node)) {
        setPanelOpen(false);
      }
    },
    [],
  );

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

  const handleMiniRailMouseEnter = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      setPanelAnchorFromClientY(event.clientY);
      openPanel();
    },
    [openPanel, setPanelAnchorFromClientY],
  );

  const handleMiniRailMouseMove = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      setPanelAnchorFromClientY(event.clientY);
    },
    [setPanelAnchorFromClientY],
  );

  const handleMiniRailFocusCapture = useCallback(
    (event: React.FocusEvent<HTMLDivElement>) => {
      if (event.target instanceof Element) {
        setPanelAnchorFromElement(event.target);
      }
      openPanel();
    },
    [openPanel, setPanelAnchorFromElement],
  );

  if (items.length === 0) {
    return null;
  }

  const isCanvas = layout === "canvas";

  return (
    <nav
      ref={wrapperRef}
      aria-label="阅读定位"
      data-testid="reader-record-navigation-rail"
      data-layout={layout}
      className={cn(
        "hidden z-30 md:flex",
        isCanvas
          ? "reader-record-navigation-rail--canvas sticky top-1/2 h-[min(72vh,42rem)] -translate-y-1/2"
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
        panelOpen={panelOpen}
        panelAnchorTopPx={panelAnchorTopPx}
        onItemClick={handleItemClick}
        onItemKeyDown={handleItemKeyDown}
        onMouseEnter={keepOpenPanel}
        onMouseLeave={handleMouseLeave}
      />

      {/* Mini rail: a column of lightweight ticks. */}
      <MiniRailTicks
        items={items}
        activeUnitId={activeUnitId}
        panelOpen={panelOpen}
        className={cn(isCanvas && "absolute inset-y-0 right-0")}
        onItemClick={handleItemClick}
        onItemKeyDown={handleItemKeyDown}
        onMouseEnter={handleMiniRailMouseEnter}
        onMouseMove={handleMiniRailMouseMove}
        onFocusCapture={handleMiniRailFocusCapture}
      />
    </nav>
  );
}

interface NavigationPanelRowProps {
  item: ReaderRecordNavigationItem;
  active: boolean;
  tabIndex?: number;
  onClick: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void;
}

function NavigationPanelRow({
  item,
  active,
  tabIndex = 0,
  onClick,
  onKeyDown,
}: NavigationPanelRowProps) {
  return (
    <li>
      <button
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
        <span className="block text-[9px] leading-snug text-muted/75">
          第 {item.fallbackIndex + 1} 段
        </span>
      </button>
    </li>
  );
}
